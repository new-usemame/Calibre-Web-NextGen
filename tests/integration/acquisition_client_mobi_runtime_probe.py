# SPDX-License-Identifier: GPL-3.0-or-later
"""Opted-in client MOBI through loopback released contracts and normal ingest.

Original files remain with the client. One mixed bundle per client and conversion
mode exercises explicit choice, actual stored format and private receipt. Faults
at the staging/publication and receipt seams must recover without downloading or
adding the same book twice. No daemon or peer download is represented here.
"""
import base64
from email import policy as email_policy
from email.parser import BytesParser
import hashlib
import json
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from acquisition_calibre_runtime_probe import APP, count_books, digest, ebook, library_format
from acquisition_torrent_metadata_runtime_probe import bencode


def run_client_mobi_runtime(root, repo, owner, other_owner, fixture, ingest, library, *, mobi_fixture, mobi_uncompressed_fixture):
    from cps.services.acquisition import admission
    from cps.services.acquisition.clients import connection_config
    from cps.services.acquisition.newznab import connection_config as indexer_config
    from cps.services.acquisition.storage import NotFound
    from cps.services.acquisition.worker import AcquisitionWorker, Paused
    from cwa_db import CWA_DB

    outcomes = []
    fixture_hashes = {path: digest(path) for path in (mobi_fixture, mobi_uncompressed_fixture)}
    for adapter in ('sabnzbd', 'nzbget', 'qbittorrent', 'transmission'):
        for conversion in (True, False):
            label = 'client-mobi-' + adapter + '-' + str(conversion)
            completed = root / label
            folder = completed / 'owned'
            folder.mkdir(parents=True)
            book = folder / 'Original.mobi'
            book.write_bytes((mobi_fixture if conversion else mobi_uncompressed_fixture).read_bytes())
            ebook(fixture, folder / 'Companion.epub', title=label,
                  body='Original legal mixed-bundle companion for ' + label)
            for path in folder.iterdir():
                path.chmod(0o755)
            originals = {p.name: (p.read_bytes(), p.stat().st_mode) for p in folder.iterdir()}
            files = [dict(name='owned/' + p.name, size=p.stat().st_size,
                          length=p.stat().st_size, progress=1, bytesCompleted=p.stat().st_size)
                     for p in sorted(folder.iterdir())]
            payload_bytes = b''.join(p.read_bytes() for p in sorted(folder.iterdir()))
            info = {'name': 'owned', 'piece length': 16384,
                    'files': [dict(length=p.stat().st_size, path=[p.name]) for p in sorted(folder.iterdir())],
                    'pieces': b''.join(hashlib.sha1(payload_bytes[i:i+16384]).digest()
                                       for i in range(0, len(payload_bytes), 16384))}
            identity = hashlib.sha1(bencode(info)).hexdigest()
            submitted, requests, descriptor = [], [], []
            torrent = adapter in ('qbittorrent', 'transmission')

            class Handler(BaseHTTPRequestHandler):
                def reply(self, value, media='application/json', cookie=False):
                    body = value if isinstance(value, bytes) else json.dumps(value).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', media)
                    self.send_header('Content-Length', str(len(body)))
                    if cookie:
                        self.send_header('Set-Cookie', 'SID=owned-mobi; HttpOnly')
                    self.end_headers()
                    self.wfile.write(body)

                def do_GET(self):
                    parsed = urlsplit(self.path)
                    query = parse_qs(parsed.query)
                    requests.append(parsed.path)
                    if parsed.path == '/descriptor':
                        assert query == {'apikey': ['fixture-indexer-key']}
                        return self.reply(descriptor[0], 'application/x-bittorrent' if torrent else 'application/x-nzb')
                    assert adapter == 'qbittorrent' and self.headers.get('Cookie') == 'SID=owned-mobi'
                    if parsed.path.endswith('/torrents/info'):
                        rows = [] if not submitted else [dict(hash=identity, tags=submitted[0]['tags'],
                            category='books', state='uploading', progress=1, amount_left=0, save_path='/downloads')]
                        return self.reply(rows)
                    assert parsed.path.endswith('/torrents/files') and query['hash'] == [identity]
                    self.reply(files)

                def do_POST(self):
                    path = urlsplit(self.path).path
                    requests.append(path)
                    body = self.rfile.read(int(self.headers['Content-Length']))
                    if path.endswith('/auth/login'):
                        assert parse_qs(body.decode()) == {'username': ['admin'], 'password': ['fixture-client-key']}
                        return self.reply(b'Ok.', 'text/plain', cookie=True)
                    if adapter in ('sabnzbd', 'qbittorrent'):
                        if self.headers['Content-Type'].startswith('multipart/form-data'):
                            message = BytesParser(policy=email_policy.default).parsebytes(
                                ('Content-Type: ' + self.headers['Content-Type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + body)
                            fields, uploads = {}, []
                            for part in message.iter_parts():
                                if part.get_filename():
                                    uploads.append(part.get_payload(decode=True))
                                else:
                                    fields[part.get_param('name', header='content-disposition')] = part.get_payload(decode=True).decode()
                            assert uploads == descriptor and b'fixture-indexer-key' not in body
                        else:
                            fields = {key: value[0] for key, value in parse_qs(body.decode()).items()}
                        if adapter == 'sabnzbd':
                            assert path == '/api' and fields['apikey'] == 'fixture-client-key'
                            if fields['mode'] == 'queue':
                                return self.reply({'queue': {'slots': []}})
                            if fields['mode'] == 'history':
                                return self.reply({'history': {'slots': [] if not submitted else [dict(
                                    nzo_id='owned-mobi', filename=submitted[0]['nzbname'], cat='books',
                                    status='Completed', loaded=False, storage='/downloads/owned')]}})
                            assert fields['mode'] == 'addfile' and fields['cat'] == 'books'
                            submitted.append(fields)
                            return self.reply({'status': True, 'nzo_ids': ['owned-mobi']})
                        assert path.endswith('/torrents/add') and self.headers.get('Cookie') == 'SID=owned-mobi'
                        assert fields['category'] == 'books' and fields['savepath'] == '/downloads' and fields['autoTMM'] == 'false'
                        # No control that pauses, deletes or changes seeding is sent.
                        assert set(fields) == {'category', 'savepath', 'tags', 'autoTMM'}
                        submitted.append(fields)
                        return self.reply(b'Ok.', 'text/plain')
                    call = json.loads(body)
                    if adapter == 'nzbget':
                        assert path == '/rpc'
                        method, params = call['method'], call['params']
                        if method == 'version':
                            return self.reply({'result': '24.8'})
                        if method == 'listgroups':
                            assert params == [0]
                            return self.reply({'result': []})
                        if method == 'history':
                            assert params == [False]
                            return self.reply({'result': [] if not submitted else [dict(NZBID=71,
                                NZBName=submitted[0][6], Category='books', Status='SUCCESS/ALL', FinalDir='/downloads/owned')]})
                        assert method == 'append' and len(params) == 10 and base64.b64decode(params[1]) == descriptor[0]
                        assert params[2:6] == ['books', 0, False, False] and params[8:] == ['ALL', []]
                        submitted.append(params)
                        return self.reply({'result': 71})
                    assert path == '/rpc'
                    method, args = call['method'], call['arguments']
                    if method == 'torrent-get':
                        rows = [] if not submitted else [dict(hashString=identity, labels=submitted[0]['labels'],
                            downloadDir='/downloads', status=6, metadataPercentComplete=1, percentDone=1,
                            leftUntilDone=0, error=0, files=files)]
                        return self.reply(dict(result='success', arguments=dict(torrents=rows)))
                    assert method == 'torrent-add' and base64.b64decode(args['metainfo']) == descriptor[0]
                    assert args['download-dir'] == '/downloads' and args['paused'] is False
                    assert set(args) == {'metainfo', 'download-dir', 'paused', 'labels'}
                    submitted.append(args)
                    self.reply(dict(result='success', arguments={'torrent-added': dict(hashString=identity)}))

                def log_message(self, *_):
                    pass

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            origin = 'http://127.0.0.1:' + str(server.server_port)
            descriptor.append(bencode(dict(announce=origin + '/announce', info=info)) if torrent else
                              ('<nzb><file><segments><segment>' + label + '</segment></segments></file></nzb>').encode())
            try:
                client = connection_config(adapter, dict(endpoint=origin + ('/api' if adapter == 'sabnzbd' else '/rpc' if adapter in ('nzbget', 'transmission') else '/'),
                    auth_kind='none' if adapter in ('sabnzbd', 'transmission') else 'basic', username='admin',
                    secret='fixture-client-key', category='books', remote_path='/downloads', local_path=str(completed), allow_mobi=True))
                client.update(private_origins=[origin], private_networks=['127.0.0.0/8'])
                download = repo.create_connection(label + ' client', adapter, client, enabled=True)
                config = indexer_config(dict(endpoint=origin + '/indexer', secret='fixture-indexer-key',
                    category='7020', client_id=download.id, tracker_origins=[origin] if torrent else []))
                config.update(private_origins=[origin], private_networks=['127.0.0.0/8'])
                source = repo.create_connection(label + ' source', 'torznab' if torrent else 'newznab', config, enabled=True)
                payload = dict(kind='acquisition', transport='torrent' if torrent else 'nzb',
                    media_type='application/x-bittorrent' if torrent else 'application/x-nzb',
                    href=origin + '/descriptor?apikey=fixture-indexer-key', release_key=hashlib.sha256(descriptor[0]).hexdigest(),
                    client_id=download.id, client_revision=download.revision, title=label)

                def worker():
                    return AcquisitionWorker(repo, root / 'acquisition-staging', ingest,
                        allowed=lambda actor: admission.account_allowed(root / 'app.db', actor),
                        enabled=lambda: admission.instance_enabled(root / 'app.db'),
                        execution_allowed=lambda job: admission.job_allowed(root / 'app.db', job.id, job.owner_id),
                        media_allowed=lambda media: admission.format_allowed(root / 'app.db', media))

                db = CWA_DB()
                db.update_cwa_settings(dict(auto_convert=int(conversion), auto_convert_target_format='epub', kindle_epub_fixer=0))
                db.con.close()
                parent = repo.create_job(owner, repo.create_offer(owner, source.id, payload), label, requires_approval=False)
                current = worker().run_once()
                assert current.state == 'awaiting_selection', (label, current)
                manifest = repo.bundle_choices(owner, parent.id)
                assert {(item['name'], item['format']) for item in manifest['candidates']} == {
                    ('Original.mobi', 'MOBI'), ('Companion.epub', 'EPUB')}, manifest
                assert worker().run_once() is None
                candidate = next(item for item in manifest['candidates'] if item['name'] == 'Original.mobi')
                if adapter == 'sabnzbd' and conversion:
                    with sqlite3.connect(root / 'app.db') as c:
                        old_formats = c.execute('SELECT config_upload_formats FROM settings').fetchone()[0]
                        c.execute("UPDATE settings SET config_upload_formats='epub,pdf'")
                    try:
                        admission.select_artifact(repo, owner, parent.id, manifest['generation'], candidate['id'])
                    except admission.AdmissionError:
                        pass
                    else:
                        raise AssertionError('A current MOBI format cap was ignored')
                    finally:
                        with sqlite3.connect(root / 'app.db') as c:
                            c.execute('UPDATE settings SET config_upload_formats=?', (old_formats,))
                job = admission.select_artifact(repo, owner, parent.id, manifest['generation'], candidate['id'])
                staged_recovery = adapter == 'sabnzbd' and not conversion
                if staged_recovery:
                    with patch.object(repo, 'prepare_publication', side_effect=Paused):
                        assert worker().run_once().state == 'staged'
                    assert not list(ingest.iterdir())
                    with sqlite3.connect(root / 'app.db') as c:
                        c.execute('UPDATE acquisition_job SET next_attempt_at=0 WHERE id=?', (job.id,))
                assert worker().run_once().state == 'importing'
                published = next(ingest.glob('*.mobi'))
                sidecar = Path(str(published) + '.cwa.json')
                assert digest(published) == digest(book) and sidecar.is_file() and not published.stat().st_mode & 0o111
                receipt_fault = adapter == 'nzbget' and conversion
                if receipt_fault:
                    with sqlite3.connect(root / 'app.db') as c:
                        c.execute("CREATE TRIGGER client_mobi_receipt_fault BEFORE INSERT ON acquisition_import_receipt BEGIN SELECT RAISE(ABORT,'client MOBI receipt fault'); END")

                def process():
                    result = subprocess.run([sys.executable, str(APP / 'scripts/ingest_processor.py'), str(published)],
                        text=True, capture_output=True, timeout=180)
                    print('CLIENT_MOBI_PROCESS', label, result.returncode, result.stdout, result.stderr, flush=True)
                    return result

                result = process()
                if receipt_fault:
                    assert result.returncode == 1 and digest(published) == digest(book) and sidecar.is_file()
                    after_add = count_books(library)
                    assert repo.get_receipt(owner, job.id) is None and repo.get_job(owner, job.id).state == 'importing'
                    with sqlite3.connect(root / 'app.db') as c:
                        c.execute('DROP TRIGGER client_mobi_receipt_fault')
                        c.execute('UPDATE settings SET config_acquisition_enabled=0')
                    result = process()
                    assert 'Content already imported; skipping duplicate add:' in result.stdout
                    assert count_books(library) == after_add
                    with sqlite3.connect(root / 'app.db') as c:
                        c.execute('UPDATE settings SET config_acquisition_enabled=1')
                assert result.returncode == 0 and not published.exists() and not sidecar.exists()
                receipt = repo.get_receipt(owner, job.id)
                expected = 'EPUB' if conversion else 'MOBI'
                stored = library_format(library, receipt.book_ids[0], expected)
                assert stored and stored.is_file(), (label, expected, receipt)
                assert receipt.source_sha256 == digest(book) and receipt.imported_sha256 == digest(stored)
                assert repo.get_job(owner, job.id).state == 'imported'
                if not conversion:
                    assert digest(stored) == digest(book)
                try:
                    repo.get_receipt(other_owner, job.id)
                except NotFound:
                    pass
                else:
                    raise AssertionError('Another owner read a client MOBI receipt')
                with sqlite3.connect(root / 'app.db') as c:
                    assert c.execute('SELECT 1 FROM user_library_book WHERE user_id=? AND book_id=?',
                        (owner, receipt.book_ids[0])).fetchone()
                    assert not c.execute('SELECT 1 FROM acquisition_import_receipt WHERE source_sha256=?',
                                         (digest(folder / 'Companion.epub'),)).fetchone()
                worker().cleanup_completed()
                assert not (root / 'acquisition-staging' / job.id).exists()
                assert len(submitted) == requests.count('/descriptor') == 1
                assert originals == {p.name: (p.read_bytes(), p.stat().st_mode) for p in folder.iterdir()}
                outcomes.append(dict(adapter=adapter, conversion=conversion, job_id=job.id,
                    book_ids=list(receipt.book_ids), actual_format=expected, source_sha256=receipt.source_sha256,
                    imported_sha256=receipt.imported_sha256, staged_recovery=staged_recovery,
                    receipt_fault_recovery=receipt_fault, remote_submissions=1, descriptor_gets=1,
                    mixed_bundle_explicit_selection=True, source_files_and_modes_unchanged=True,
                    private_receipt=True, owned_cleanup=True, seeding_controls_unchanged=True))
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
    assert all(digest(path) == value for path, value in fixture_hashes.items())
    return dict(outcomes=outcomes, real_loopback_http=True, full_processor_subprocess=True,
                current_global_format_cap=True, peer_download_or_running_released_client=False)
