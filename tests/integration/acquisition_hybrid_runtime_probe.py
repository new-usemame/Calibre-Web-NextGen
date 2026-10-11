# SPDX-License-Identifier: GPL-3.0-or-later
"""Hybrid descriptors through real loopback HTTP, workers and normal ingest.

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
from urllib.parse import parse_qs, urlsplit

from acquisition_calibre_runtime_probe import APP, ebook, digest, library_format


def run_hybrid_runtime(root, repo, owner, other_owner, fixture, ingest, library):
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
    cases = [('qbittorrent', '1.2.19.0', False), ('qbittorrent', '2.0.11.0', False),
             ('transmission', None, False), ('qbittorrent', '2.0.11.0', True),
             ('transmission', None, True)]
    for number, (adapter, engine, multi) in enumerate(cases):
        label = 'hybrid-'+str(number)
        completed = root/label
        folder = completed/'owned' if multi else completed
        folder.mkdir(parents=True)
        names = ['A.epub', 'B.epub'] if multi else ['Original.epub']
        for name in names:
            ebook(fixture, folder/name, title=label+' '+name,
                  body='Original legal '+label+' '+name+' hybrid chapter.')
            (folder/name).chmod(0o755)
        resources = [(name, (folder/name).read_bytes()) for name in names]
        files = [(('owned/' if multi else '')+name, len(body)) for name, body in resources]
        originals = {p.relative_to(completed).as_posix(): (p.read_bytes(), p.stat().st_mode)
                     for p in completed.rglob('*') if p.is_file()}
        submitted, methods, descriptors, hashes = [], [], [], {}
        identity = []

        class Handler(BaseHTTPRequestHandler):
            def reply(self, value, media='application/json', cookie=False):
                body = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Type', media)
                self.send_header('Content-Length', str(len(body)))
                if cookie:
                    self.send_header('Set-Cookie', 'SID=owned-hybrid; HttpOnly')
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query)
                methods.append(parsed.path)
                if parsed.path == '/descriptor':
                    assert query == {'apikey': ['fixture-indexer-key']}
                    return self.reply(descriptors[0], 'application/x-bittorrent')
                assert self.headers.get('Cookie') == 'SID=owned-hybrid'
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
                if adapter == 'qbittorrent':
                    assert path.endswith('/torrents/add') and not submitted
                    assert self.headers.get('Cookie') == 'SID=owned-hybrid'
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
                    return self.reply(dict(result='success', arguments=dict(torrents=rows)))
                assert method == 'torrent-add' and not submitted
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
        raw, hashes = metainfo(resources, name='owned' if multi else names[0], announce=origin+'/announce', multi=multi)
        descriptors.append(raw)
        identity.append(hashes['v2_truncated'] if engine and engine.startswith('2.') else hashes['v1'])
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
            assert current.state == ('awaiting_selection' if multi else 'importing'), current
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
                    raise AssertionError('Another owner read a hybrid manifest')
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
                result = subprocess.run(['python3', str(APP/'scripts/ingest_processor.py'), str(published)],
                    text=True, capture_output=True, timeout=180)
                print('HYBRID PROCESS '+label+' '+name+' EXIT '+str(result.returncode)+'\n'+result.stdout+result.stderr, flush=True)
                assert result.returncode == 0
                receipt = repo.get_receipt(owner, job.id)
                assert receipt and receipt.source_sha256 == digest(folder/name)
                stored = library_format(library, receipt.book_ids[0], 'EPUB')
                assert stored and digest(stored) == receipt.imported_sha256
                assert repo.get_job(owner, job.id).state == 'imported'
                try:
                    repo.get_receipt(other_owner, job.id)
                except NotFound:
                    pass
                else:
                    raise AssertionError('Another owner read a hybrid receipt')
                with sqlite3.connect(root/'app.db') as connection:
                    assert connection.execute('SELECT 1 FROM user_library_book WHERE user_id=? AND book_id=?',
                        (owner, receipt.book_ids[0])).fetchone()
                worker().cleanup_completed()
                assert not published.exists() and not Path(str(published)+'.cwa.json').exists()
                assert not (root/'acquisition-staging'/job.id).exists()
                receipts.append(dict(book_ids=list(receipt.book_ids), source_sha256=receipt.source_sha256,
                    imported_sha256=receipt.imported_sha256))
            assert len(submitted) == 1
            assert originals == {p.relative_to(completed).as_posix(): (p.read_bytes(), p.stat().st_mode)
                                 for p in completed.rglob('*') if p.is_file()}
            outcomes.append(dict(adapter=adapter, engine=engine, multi=multi, external_id=identity[0],
                v1_infohash=hashes['v1'], v2_infohash=hashes['v2'], receipts=receipts,
                exact_descriptor_submitted=True, source_files_and_modes_unchanged=True, remote_submissions=1,
                private_receipts=True, owned_cleanup=True, fresh_worker_reused_submission=True,
                build_info_before_submit=(methods.index('/api/v2/app/buildInfo') < methods.index('/api/v2/torrents/add'))
                    if adapter == 'qbittorrent' else None))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    return dict(outcomes=outcomes, real_loopback_http=True, full_processor_subprocess=True,
                peer_download_or_running_released_client=False)
