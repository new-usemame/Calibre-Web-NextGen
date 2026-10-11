# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-v2 original descriptors through real loopback HTTP, workers and normal ingest.

The servers exercise released RPC/WebAPI contracts, not peer downloads or a
running released client. Original metainfo fixtures are independently checked
with a sessionless released creator/parser outside this normal runtime gate.
"""
import base64
from email import policy as email_policy
from email.parser import BytesParser
import hashlib
import json
import sqlite3
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import runpy
import sys
import zipfile
from urllib.parse import parse_qs, urlsplit

from acquisition_calibre_runtime_probe import APP, ebook, digest, library_format, count_books


def run_torrent_v2_runtime(root, repo, owner, other_owner, fixture, ingest, library):
    from cps.services.acquisition import admission
    from cps.services.acquisition.clients import connection_config
    from cps.services.acquisition.newznab import connection_config as indexer_config
    from cps.services.acquisition.storage import NotFound
    from cps.services.acquisition.worker import AcquisitionWorker
    from cwa_db import CWA_DB

    fixture_module = APP/'tests/fixtures/virtual_library_hybrid.py'
    if not fixture_module.exists():
        fixture_module = Path('/tmp/virtual_library_hybrid.py')
    metainfo = runpy.run_path(str(fixture_module))['metainfo']
    db = CWA_DB()
    db.update_cwa_settings(dict(auto_convert=1, auto_convert_target_format='epub', kindle_epub_fixer=0))
    db.con.close()
    outcomes = []
    cases = [('single', 'qbittorrent', '2.0.11.0'), ('multi', 'qbittorrent', '2.0.11.0'),
             ('layered', 'qbittorrent', '2.0.11.0'), ('lt1-retry', 'qbittorrent', '1.2.19.0'),
             ('transmission-refusal', 'transmission', None),
             ('collision-retry', 'qbittorrent', '2.0.11.0'),
             ('hybrid-collision-retry', 'transmission', None)]
    refusals = []
    collision_retries = []
    for shape, adapter, engine in cases:
        label = 'pure-v2-'+shape
        multi = shape == 'multi'
        pure = shape != 'hybrid-collision-retry'
        collision = [shape in ('collision-retry', 'hybrid-collision-retry')]
        completed = root/label
        folder = completed/'owned' if multi else completed
        folder.mkdir(parents=True)
        names = ['A.epub', 'B.epub'] if multi else ['Original.epub']
        for name in names:
            ebook(fixture, folder/name, title=label+' '+name,
                  body='Original legal '+label+' '+name+' pure-v2 chapter.')
            (folder/name).chmod(0o755)
        if shape == 'layered':
            with zipfile.ZipFile(folder/names[0], 'a', compression=zipfile.ZIP_STORED) as archive:
                entry = zipfile.ZipInfo('compatibility-unused.bin', (2026, 10, 4, 0, 0, 0))
                archive.writestr(entry, hashlib.shake_256(b'Original legal pure-v2 layer fixture').digest(7*32768+123))
            with zipfile.ZipFile(folder/names[0]) as archive:
                assert archive.testzip() is None and archive.read('mimetype') == b'application/epub+zip'
        resources = [(name, (folder/name).read_bytes()) for name in names]
        files = [(('owned/' if multi else '')+name, len(body)) for name, body in resources]
        padding_files = []
        if multi:
            for _, body in resources:
                size = (-len(body)) % 16384
                if size:
                    padding_files.append(('owned/.pad/'+str(size), size))
            files.extend(padding_files)
        originals = {p.relative_to(completed).as_posix(): (p.read_bytes(), p.stat().st_mode)
                     for p in completed.rglob('*') if p.is_file()}
        submitted, methods, descriptors, hashes = [], [], [], {}
        identity = []
        downstream_posts = []
        submission_posts = []

        class Handler(BaseHTTPRequestHandler):
            def reply(self, value, media='application/json', cookie=False):
                body = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Type', media)
                self.send_header('Content-Length', str(len(body)))
                if cookie:
                    self.send_header('Set-Cookie', 'SID=owned-pure-v2; HttpOnly')
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query)
                methods.append(parsed.path)
                if parsed.path == '/descriptor':
                    assert query == {'apikey': ['fixture-indexer-key']}
                    return self.reply(descriptors[0], 'application/x-bittorrent')
                assert self.headers.get('Cookie') == 'SID=owned-pure-v2'
                if parsed.path.endswith('/app/buildInfo'):
                    return self.reply({'libtorrent': engine})
                if parsed.path.endswith('/torrents/info'):
                    if 'hashes' in query:
                        assert query['hashes'] == identity
                    else:
                        assert query['category'] == ['books']
                    rows = [] if not submitted else [dict(hash=identity[0],
                        tags=submitted[0]['tags'], category='books', state='uploading',
                        progress=1, amount_left=0, save_path='/downloads')]
                    if collision[0]:
                        assert not submitted
                        rows = [dict(hash=identity[0], tags='unrelated-owner', category='books')]
                    return self.reply(rows)
                assert parsed.path.endswith('/torrents/files') and query['hash'] == identity
                self.reply([dict(name=name, size=size, progress=1) for name, size in files])

            def do_POST(self):
                path = urlsplit(self.path).path
                methods.append(path)
                body = self.rfile.read(int(self.headers['Content-Length']))
                if path.endswith('/auth/login'):
                    assert parse_qs(body.decode()) == {'username': ['admin'], 'password': ['fixture-client-key']}
                    return self.reply(b'Ok.', 'text/plain', cookie=True)
                downstream_posts.append(path)
                if adapter == 'qbittorrent':
                    assert path.endswith('/torrents/add') and not submitted and not collision[0]
                    submission_posts.append(path)
                    assert self.headers.get('Cookie') == 'SID=owned-pure-v2'
                    with sqlite3.connect(root/'app.db') as connection:
                        fence = connection.execute('SELECT submission_started,external_id FROM acquisition_job WHERE id=?', (parent.id,)).fetchone()
                    assert fence[0] is not None and fence[1] is None, fence
                    message = BytesParser(policy=email_policy.default).parsebytes(
                        ('Content-Type: '+self.headers['Content-Type']+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+body)
                    fields = {}
                    uploads = []
                    for part in message.iter_parts():
                        if part.get_filename():
                            uploads.append(part.get_payload(decode=True))
                        else:
                            fields[part.get_param('name', header='content-disposition')] = part.get_payload(decode=True).decode()
                    assert uploads == descriptors
                    assert fields['category'] == 'books' and fields['savepath'] == '/downloads' and fields['autoTMM'] == 'false'
                    submitted.append(fields)
                    return self.reply(dict(success_count=1, failure_count=0,
                        pending_count=0, added_torrent_ids=identity))
                assert path == '/rpc'
                request = json.loads(body)
                method, args = request['method'], request['arguments']
                if method == 'torrent-get':
                    if 'ids' in args:
                        assert args['ids'] == identity
                    rows = [] if not submitted else [dict(hashString=identity[0],
                        labels=submitted[0]['labels'], downloadDir='/downloads', status=6,
                        metadataPercentComplete=1, percentDone=1, leftUntilDone=0, error=0,
                        files=[dict(name=name, length=size, bytesCompleted=size) for name, size in files])]
                    if collision[0]:
                        assert not submitted
                        rows = [dict(hashString=identity[0], labels=['unrelated-owner'])]
                    return self.reply(dict(result='success', arguments=dict(torrents=rows)))
                assert method == 'torrent-add' and not submitted and not collision[0]
                submission_posts.append(method)
                assert base64.b64decode(args['metainfo']) == descriptors[0]
                assert args['download-dir'] == '/downloads' and args['paused'] is False
                submitted.append(args)
                self.reply(dict(result='success', arguments={'torrent-added': dict(hashString=identity[0])}))

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        origin = 'http://127.0.0.1:'+str(server.server_port)
        raw, hashes = metainfo(resources, name='owned' if multi else names[0], announce=origin+'/announce', multi=multi,
                               piece_length=32768 if shape == 'layered' else 16384, pure_v2=pure)
        assert (hashes['v1'] is None) == pure
        assert hashes['piece_layer_count'] == (1 if shape == 'layered' else 0)
        evidence = root/'pure-v2-fixtures'/shape
        evidence.mkdir(parents=True)
        (evidence/'original.torrent').write_bytes(raw)
        for name, body in resources:
            (evidence/name).write_bytes(body)
        (evidence/'expected.json').write_text(json.dumps(dict(hashes=hashes, pure_v2=pure, name='owned' if multi else names[0], multi=multi, piece_length=32768 if shape == 'layered' else 16384, reported_padding=padding_files, resources=[dict(name=name,size=len(body),sha256=hashlib.sha256(body).hexdigest()) for name,body in resources])))
        descriptors.append(raw)
        identity.append(hashes['v2_truncated'] if adapter == 'qbittorrent' else hashes['v1'])
        try:
            config = connection_config(adapter, dict(endpoint=origin+('/rpc' if adapter == 'transmission' else '/'),
                auth_kind='none' if adapter == 'transmission' else 'basic', username='admin',
                secret='fixture-client-key', category='books', remote_path='/downloads', local_path=str(completed)))
            config.update(private_origins=[origin], private_networks=['127.0.0.0/8'])
            download = repo.create_connection(label+' client', adapter, config, enabled=True)
            config = indexer_config(dict(endpoint=origin+'/indexer', secret='fixture-indexer-key',
                category='7020', client_id=download.id, tracker_origins=[origin]))
            config.update(private_origins=[origin], private_networks=['127.0.0.0/8'])
            source = repo.create_connection(label+' source', 'torznab', config, enabled=True)
            payload = dict(kind='acquisition', transport='torrent', media_type='application/x-bittorrent',
                href=origin+'/descriptor?apikey=fixture-indexer-key', release_key=hashlib.sha256(raw).hexdigest(),
                client_id=download.id, client_revision=download.revision, title=label)

            def worker():
                return AcquisitionWorker(repo, root/'acquisition-staging', ingest,
                    allowed=lambda user: admission.account_allowed(root/'app.db', user),
                    enabled=lambda: admission.instance_enabled(root/'app.db'),
                    execution_allowed=lambda job: admission.job_allowed(root/'app.db', job.id, job.owner_id))

            parent = repo.create_job(owner, repo.create_offer(owner, source.id, payload), label, requires_approval=False)
            current = worker().run_once()
            if shape in ('lt1-retry', 'transmission-refusal'):
                assert current.state == 'failed' and current.error_code == 'unsupported_client_version', current
                with sqlite3.connect(root/'app.db') as connection:
                    assert connection.execute('SELECT submission_started,external_id,submission_key FROM acquisition_job WHERE id=?', (parent.id,)).fetchone() == (None, None, None)
                assert not downstream_posts and not submitted and methods.count('/descriptor') == 1
                refusals.append(dict(adapter=adapter, engine=engine, error_code=current.error_code, downstream_posts=0, submission_started=None, descriptor_gets=1))
                if adapter == 'transmission':
                    continue
                engine = '2.0.11.0'
                repo.retry(owner, parent.id)
                current = worker().run_once()
            if collision[0]:
                assert current.state == 'failed' and current.error_code == 'torrent_already_exists', current
                with sqlite3.connect(root/'app.db') as connection:
                    refused = connection.execute('SELECT submission_started,external_id,submission_key,submission_invalid FROM acquisition_job WHERE id=?', (parent.id,)).fetchone()
                assert refused == (None, None, None, None), refused
                assert not submitted and not submission_posts and methods.count('/descriptor') == 1
                assert originals == {p.relative_to(completed).as_posix(): (p.read_bytes(), p.stat().st_mode)
                                     for p in completed.rglob('*') if p.is_file()}
                collision_retries.append(dict(adapter=adapter, error_code=current.error_code,
                    remote_submissions_before_retry=0, submission_started=None, external_id=None,
                    no_submission_attempt_issued=True, submission_key=None, submission_invalid=None,
                    original_client_files_and_modes_preserved=True))
                # Model external removal of the unrelated conflict; CWNG sends no removal.
                collision[0] = False
                repo.retry(owner, parent.id)
                current = worker().run_once()
            assert current.state == ('awaiting_selection' if multi else 'importing'), ('pure-v2 admission did not reach normal ingest', current)
            with sqlite3.connect(root/'app.db') as connection:
                assert connection.execute('SELECT external_id FROM acquisition_job WHERE id=?', (parent.id,)).fetchone() == (identity[0],)
            manifest = repo.bundle_choices(owner, parent.id) if multi else None
            if multi:
                assert len(manifest['candidates']) == 2 and worker().run_once() is None
                try:
                    repo.bundle_choices(other_owner, parent.id)
                except NotFound:
                    pass
                else:
                    raise AssertionError('Another owner read a pure-v2 manifest')
            receipts = []
            for name in names:
                if multi:
                    candidate = next(item for item in manifest['candidates'] if item['name'] == name)
                    job = admission.select_artifact(repo, owner, parent.id, manifest['generation'], candidate['id'])
                    assert worker().run_once().state == 'importing'
                else:
                    job = parent
                published = next(ingest.glob('*.epub'))
                assert published.read_bytes() == (folder/name).read_bytes() and not published.stat().st_mode & 0o111
                sidecar = Path(str(published)+'.cwa.json')
                with sqlite3.connect(root/'app.db') as connection:
                    connection.execute('UPDATE acquisition_job SET next_attempt_at=0 WHERE id=?', (job.id,))
                assert worker().run_once().state == 'importing'
                assert len(submitted) == 1 and published.read_bytes() == (folder/name).read_bytes()
                receipt_fault = shape == 'single'
                if receipt_fault:
                    with sqlite3.connect(root/'app.db') as connection:
                        connection.execute("CREATE TRIGGER pure_v2_receipt_fault BEFORE INSERT ON acquisition_import_receipt BEGIN SELECT RAISE(ABORT,'pure-v2 receipt fault'); END")
                def process():
                    result = subprocess.run([sys.executable, str(APP/'scripts/ingest_processor.py'), str(published)],
                        text=True, capture_output=True, timeout=180)
                    print('PURE_V2 PROCESS '+label+' '+name+' EXIT '+str(result.returncode)+'\n'+result.stdout+result.stderr, flush=True)
                    return result
                result = process()
                if receipt_fault:
                    assert result.returncode == 1 and published.is_file() and sidecar.is_file()
                    assert repo.get_receipt(owner, job.id) is None and digest(published) == digest(folder/name)
                    after_add = count_books(library)
                    with sqlite3.connect(root/'app.db') as connection:
                        connection.execute('DROP TRIGGER pure_v2_receipt_fault')
                        connection.execute('UPDATE settings SET config_acquisition_enabled=0')
                    result = process()
                    assert 'Content already imported; skipping duplicate add:' in result.stdout
                    assert count_books(library) == after_add
                    with sqlite3.connect(root/'app.db') as connection:
                        connection.execute('UPDATE settings SET config_acquisition_enabled=1')
                assert result.returncode == 0, result.returncode
                receipt = repo.get_receipt(owner, job.id)
                assert receipt and receipt.source_sha256 == digest(folder/name)
                stored = library_format(library, receipt.book_ids[0], 'EPUB')
                assert stored and digest(stored) == receipt.imported_sha256 == receipt.source_sha256
                assert repo.get_job(owner, job.id).state == 'imported'
                try:
                    repo.get_receipt(other_owner, job.id)
                except NotFound:
                    pass
                else:
                    raise AssertionError('Another owner read a pure-v2 receipt')
                with sqlite3.connect(root/'app.db') as connection:
                    assert connection.execute('SELECT 1 FROM user_library_book WHERE user_id=? AND book_id=?',
                        (owner, receipt.book_ids[0])).fetchone()
                worker().cleanup_completed()
                assert not published.exists() and not Path(str(published)+'.cwa.json').exists()
                assert not (root/'acquisition-staging'/job.id).exists()
                receipts.append(dict(book_ids=list(receipt.book_ids), source_sha256=receipt.source_sha256,
                    imported_sha256=receipt.imported_sha256))
            assert len(submitted) == len(submission_posts) == 1
            assert methods.count('/descriptor') == (2 if shape in ('lt1-retry', 'collision-retry', 'hybrid-collision-retry') else 1)
            assert worker().run_once() is None
            assert originals == {p.relative_to(completed).as_posix(): (p.read_bytes(), p.stat().st_mode)
                                 for p in completed.rglob('*') if p.is_file()}
            outcomes.append(dict(shape=shape, descriptor_sha256=hashlib.sha256(raw).hexdigest(),
                fixture_evidence=str(evidence), piece_layer_count=hashes['piece_layer_count'],
                descriptor_gets=methods.count('/descriptor'), compatible_descriptor_gets=1, receipt_fault_recovery=shape == 'single',
                mixed_bundle_explicit_selection=multi, synthetic_padding_reported=bool(padding_files),
                synthetic_padding_excluded_from_choices=multi and len(manifest['candidates']) == 2,
                adapter=adapter, engine=engine, multi=multi, pure_v2=pure, external_id=identity[0],
                v1_infohash=hashes['v1'], v2_infohash=hashes['v2'], receipts=receipts,
                exact_descriptor_submitted=True, source_files_and_modes_unchanged=True, remote_submissions=1,
                private_receipts=True, owned_cleanup=True, fresh_worker_reused_submission=True,
                build_info_before_submit=(methods.index('/api/v2/app/buildInfo') < methods.index('/api/v2/torrents/add'))
                    if adapter == 'qbittorrent' else None))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    return dict(outcomes=outcomes, refusals=refusals, collision_retries=collision_retries, real_loopback_http=True, full_processor_subprocess=True,
                peer_download_or_running_released_client=False)
