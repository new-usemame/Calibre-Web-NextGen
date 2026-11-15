# SPDX-License-Identifier: GPL-3.0-or-later
"""Original single-topic v2 magnets through owned HTTP and normal Calibre ingest.

No running released client or peers. The fixture builder is independently joined
to sessionless libtorrent 2.0.11 creator/parser output outside this runtime gate.
Native writes use production admission: public POST availability still requires
s6, as in the preceding normal wrapper. Catalog and choices use real API GETs.
"""
import hashlib
import json
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlencode, urlsplit
import xml.etree.ElementTree as ET
import zipfile

from acquisition_calibre_runtime_probe import APP, ebook, digest, library_format, count_books


CASES = ('single', 'multi', 'layered', 'trackerless', 'lt1-retry',
         'unknown-retry', 'old-api-retry', 'transmission-refusal', 'collision-retry',
         'pending-restart', 'missing-full-restart', 'unavailable-restart',
         'same-short-different-full', 'lost-ack-tag-recovery')


def _native_quote(value):
    # Released make_magnet_uri uses lowercase percent escapes.
    import re
    return re.sub(r'%[0-9A-F]{2}', lambda match: match[0].lower(), quote(value, safe=''))


def _api_client(owner):
    """Actual blueprint/session loader; no replacement current_user or transport."""
    from flask import Flask, session
    from cps import ub, limiter, config, config_sql, cli_param
    if not hasattr(config, 'config_allow_reverse_proxy_header_login'):
        key, error = config_sql.get_encryption_key(str(Path(ub.app_DB_path).parent))
        assert not error, 'Owned fixture configuration key could not be created'
        config.init_config(ub.session, key, cli_param)
    from cps.api import api_v1
    from cps.api import acquisition  # register the production routes
    from cps.cw_login import LoginManager, login_user
    from cps.usermanagement import load_user
    app = Flask(__name__)
    app.config.update(SECRET_KEY='owned-native-v2-magnet-session', RATELIMIT_ENABLED=False)
    manager = LoginManager()
    manager.init_app(app)
    manager.user_loader(load_user)
    if limiter is not None:
        limiter.init_app(app)
    app.register_blueprint(api_v1)
    client = app.test_client()
    with app.test_request_context('/'):
        user = ub.session.query(ub.User).filter(ub.User.id == owner).one()
        assert login_user(user)
        ub.store_user_session()
        authenticated = dict(session)
    with client.session_transaction() as saved:
        saved.update(authenticated)
    return client


def run_v2_magnet_runtime(root, repo, owner, other_owner, fixture, ingest, library,
                          *, cases=CASES, discover=True, accepted_recovery=False, evidence_dir=None):
    """Full hashes gate publication even after restart and uncertain acknowledgement.

    accepted_recovery is an external RED diagnostic: seed an already accepted
    fence in real SQLite, avoiding frozen16's earlier parser rejection. The full
    normal wrapper never uses it and always requires exactly one real URL add.
    """
    from cps import constants
    from cps.services.acquisition import admission
    from cps.services.acquisition.clients import connection_config
    from cps.services.acquisition.newznab import connection_config as indexer_config
    from cps.services.acquisition.storage import NotFound
    from cps.services.acquisition.worker import AcquisitionWorker
    from cwa_db import CWA_DB

    builder = APP/'tests/fixtures/virtual_library_hybrid.py'
    if not builder.exists():
        builder = Path('/tmp/virtual_library_hybrid.py')
    metainfo = runpy.run_path(str(builder))['metainfo']
    db = CWA_DB()
    db.update_cwa_settings(dict(auto_convert=1, auto_convert_target_format='epub', kindle_epub_fixer=0))
    db.con.close()
    outcomes, refusals, collisions = [], [], []
    api, other_api = _api_client(owner), _api_client(other_owner)

    for shape in cases:
        assert shape in CASES, shape
        label = 'v2-magnet-'+shape
        multi = shape == 'multi'
        recovery = shape in ('pending-restart', 'missing-full-restart', 'unavailable-restart', 'same-short-different-full')
        adapter = 'transmission' if shape == 'transmission-refusal' else 'qbittorrent'
        engine = '1.2.19.0' if shape == 'lt1-retry' else None if shape == 'unknown-retry' else '2.0.11.0'
        api_version = '2.9.3' if shape == 'old-api-retry' else '2.11.2'
        collision = shape == 'collision-retry'
        metadata = 'pending' if recovery else 'verified'
        completed = root/label
        folder = completed/'owned' if multi else completed
        folder.mkdir(parents=True)
        names = ['A.epub', 'B.epub'] if multi else ['Original.epub']
        for name in names:
            ebook(fixture, folder/name, title=label+' '+name,
                  body='Original legal '+label+' '+name+' chapter.')
            (folder/name).chmod(0o640)
        if shape == 'layered':
            with zipfile.ZipFile(folder/names[0], 'a', compression=zipfile.ZIP_STORED) as archive:
                archive.writestr(zipfile.ZipInfo('compatibility-unused.bin', (2026, 10, 4, 0, 0, 0)),
                                 hashlib.shake_256(b'original legal v2 magnet layer').digest(7*32768+123))
        resources = [(name, (folder/name).read_bytes()) for name in names]
        files = [(('owned/' if multi else '')+name, len(body)) for name, body in resources]
        pads = [('owned/.pad/'+str((-len(body)) % 16384), (-len(body)) % 16384)
                for _, body in resources if multi and (-len(body)) % 16384]
        files.extend(pads)
        originals = {p.relative_to(completed).as_posix(): (p.read_bytes(), p.stat().st_mode)
                     for p in completed.rglob('*') if p.is_file()}
        submitted, methods, adds, events, server_errors = [], [], [], [], []
        requested, full, identity, raw = None, None, None, None
        parent = None

        def fence():
            with sqlite3.connect(root/'app.db') as connection:
                return connection.execute('SELECT external_id,submission_started,submission_key,submission_invalid FROM acquisition_job WHERE id=?', (parent.id,)).fetchone()

        def unchanged():
            return originals == {p.relative_to(completed).as_posix(): (p.read_bytes(), p.stat().st_mode)
                                 for p in completed.rglob('*') if p.is_file()}

        class Handler(BaseHTTPRequestHandler):
            def reply(self, value, media='application/json', status=200, cookie=False):
                body = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', media)
                self.send_header('Content-Length', str(len(body)))
                if cookie:
                    self.send_header('Set-Cookie', 'SID=owned-v2-magnet; HttpOnly')
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                try:
                    self.get()
                except Exception as error:
                    server_errors.append(repr(error))
                    raise

            def get(self):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query)
                methods.append(parsed.path)
                if parsed.path == '/indexer':
                    assert query['apikey'] == ['fixture-indexer-key']
                    if query['t'] == ['caps']:
                        return self.reply(b'<caps><server title="Owned magnets"/><searching><book-search available="yes" supportedParams="q"/></searching><categories><category id="7020"/></categories></caps>', 'application/xml')
                    assert query['t'] == ['book']
                    rss = ET.Element('rss')
                    item = ET.SubElement(ET.SubElement(rss, 'channel'), 'item')
                    ET.SubElement(item, 'title').text = label
                    ET.SubElement(item, 'guid').text = label
                    ET.SubElement(item, 'enclosure', url=requested, type='application/x-bittorrent')
                    return self.reply(ET.tostring(rss), 'application/xml')
                assert self.headers.get('Cookie') == 'SID=owned-v2-magnet'
                if parsed.path.endswith('/app/buildInfo'):
                    return self.reply(dict(libtorrent=engine))
                if parsed.path.endswith('/app/webapiVersion'):
                    return self.reply(api_version.encode(), 'text/plain')
                if parsed.path.endswith('/torrents/info'):
                    if 'hashes' in query:
                        assert query['hashes'] == [identity]
                    else:
                        assert query['category'] == ['books']
                    rows = [] if not submitted else [dict(hash=identity, tags=submitted[0]['tags'],
                        category='books', state='uploading', progress=1, amount_left=0, save_path='/downloads')]
                    if collision:
                        assert not submitted
                        rows = [dict(hash=identity, tags='unrelated-owner', category='books')]
                    return self.reply(rows)
                assert query['hash'] == [identity], query
                if parsed.path.endswith('/torrents/properties'):
                    events.append(dict(kind='properties', metadata=metadata))
                    if metadata == 'unavailable':
                        return self.reply(b'unavailable', 'text/plain', status=503)
                    actual = full
                    if metadata == 'mismatch':
                        actual = full[:40]+('0' if full[40] != '0' else '1')+full[41:]
                        assert actual != full and actual[:40] == identity
                    value = dict(has_metadata=metadata != 'pending', hash=identity,
                                 infohash_v1='', infohash_v2=actual)
                    if metadata == 'missing':
                        value.pop('infohash_v2')
                    return self.reply(value)
                assert parsed.path.endswith('/torrents/files'), parsed.path
                events.append(dict(kind='files', metadata=metadata))
                return self.reply([dict(name=name, size=size, progress=1) for name, size in files])

            def do_POST(self):
                try:
                    self.post()
                except Exception as error:
                    server_errors.append(repr(error))
                    raise

            def post(self):
                path = urlsplit(self.path).path
                methods.append(path)
                body = self.rfile.read(int(self.headers['Content-Length']))
                if path.endswith('/auth/login'):
                    assert parse_qs(body.decode()) == {'username': ['admin'], 'password': ['fixture-client-key']}
                    return self.reply(b'Ok.', 'text/plain', cookie=True)
                if adapter == 'transmission':
                    request = json.loads(body)
                    assert request['method'] == 'torrent-get', 'Unqualified Transmission issued torrent-add'
                    return self.reply(dict(result='success', arguments=dict(torrents=[])))
                assert path.endswith('/torrents/add') and not submitted and not collision
                assert self.headers.get('Content-Type', '').startswith('application/x-www-form-urlencoded'), 'Magnet must use URL form, never metainfo upload'
                fields = {key: value[0] for key, value in parse_qs(body.decode()).items()}
                assert fields['urls'] == requested, (fields['urls'], requested)
                assert fields['category'] == 'books' and fields['savepath'] == '/downloads' and fields['autoTMM'] == 'false'
                before = fence()
                assert before[0] is None and before[1] is not None and before[2] is not None, before
                submitted.append(fields)
                adds.append(dict(path=path, fields=fields, fence_at_add=before))
                events.append(dict(kind='add', original_uri=requested))
                # Valid HTTP but lost usable acknowledgement: the accepted client
                # row is discoverable only by its caller-attributed tag on restart.
                return self.reply({} if shape == 'lost-ack-tag-recovery' else b'Ok.',
                                  'application/json' if shape == 'lost-ack-tag-recovery' else 'text/plain')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        origin = 'http://127.0.0.1:'+str(server.server_port)
        raw, hashes = metainfo(resources, name='owned' if multi else names[0], announce=origin+'/announce',
                               multi=multi, piece_length=32768 if shape == 'layered' else 16384, pure_v2=True)
        assert hashes['v1'] is None
        full, identity = hashes['v2'], hashes['v2_truncated']
        generated = 'magnet:?xt=urn:btmh:1220'+full+'&dn='+_native_quote('owned' if multi else names[0])+'&tr='+_native_quote(origin+'/announce')
        requested = (urlencode(dict(xt='urn:btmh:1220'+full, dn=names[0])) if shape == 'trackerless' else None)
        requested = 'magnet:?'+requested if requested else generated
        evidence = (Path(evidence_dir) if evidence_dir is not None else root/'v2-magnet-fixtures')/shape
        evidence.mkdir(parents=True)
        (evidence/'original.torrent').write_bytes(raw)
        (evidence/'original-uri.txt').write_text(requested)
        for name, body in resources:
            (evidence/name).write_bytes(body)
        (evidence/'expected.json').write_text(json.dumps(dict(hashes=hashes, pure_v2=True,
            name='owned' if multi else names[0], multi=multi, generated_uri=generated, original_uri=requested,
            piece_length=32768 if shape == 'layered' else 16384, reported_padding=pads,
            resources=[dict(name=name, size=len(body), sha256=hashlib.sha256(body).hexdigest()) for name, body in resources])))
        try:
            config = connection_config(adapter, dict(endpoint=origin+('/rpc' if adapter == 'transmission' else '/'),
                auth_kind='none' if adapter == 'transmission' else 'basic', username='admin',
                secret='fixture-client-key', category='books', remote_path='/downloads', local_path=str(completed)))
            config.update(private_origins=[origin], private_networks=['127.0.0.0/8'])
            download = repo.create_connection(label+' client', adapter, config, enabled=True)
            config = indexer_config(dict(endpoint=origin+'/indexer', secret='fixture-indexer-key', category='7020',
                client_id=download.id, preset='torznab', tracker_origins=[origin]))
            # Privileged isolated fixture exception, never public admin policy.
            config.update(private_origins=[origin], private_networks=['127.0.0.0/8'])
            source = repo.create_connection(label+' source', 'newznab', config, enabled=True)
            payload = dict(kind='acquisition', transport='torrent', media_type='application/x-bittorrent',
                href=requested, release_key=hashlib.sha256(raw).hexdigest(), client_id=download.id,
                client_revision=download.revision, title=label)
            if discover:
                # The privileged loopback exception must never pass public
                # administrator source-probe revalidation.
                with sqlite3.connect(root/'app.db') as connection:
                    previous_role = connection.execute('SELECT role FROM user WHERE id=?', (owner,)).fetchone()[0]
                    connection.execute('UPDATE user SET role=? WHERE id=?', (previous_role | constants.ROLE_ADMIN, owner))
                try:
                    before_probe = list(methods)
                    probe_response = api.post('/api/v1/admin/acquisition/connections/'+source.id+'/probe', json={})
                    assert probe_response.status_code == 502, probe_response.get_data(as_text=True)
                    # CatalogError is deliberately redacted at the API seam.
                    assert probe_response.get_json()['error']['code'] == 'source_unavailable', probe_response.get_json()
                    assert methods == before_probe, 'Rejected source probe reached loopback HTTP'
                    (evidence/'public-source-probe.json').write_text(json.dumps(probe_response.get_json()))
                finally:
                    with sqlite3.connect(root/'app.db') as connection:
                        connection.execute('UPDATE user SET role=? WHERE id=?', (previous_role, owner))
                response = api.get('/api/v1/acquisition/catalog', query_string=dict(connection=source.id, q=label))
                assert response.status_code == 200, response.get_data(as_text=True)
                page = response.get_json()
                (evidence/'catalog.json').write_text(json.dumps(page))
                offers = page['publications'][0]['offers']
                assert len(offers) == 1, ('Original legal pure-v2 magnet must be offered by the production catalog', page)
                offer_id = offers[0]['offer_id']
                assert repo.offer_payload(owner, offer_id, source.id).offer['href'] == requested
            else:
                offer_id = repo.create_offer(owner, source.id, payload)
            parent = admission.create_request(repo, owner, connection_id=source.id, offer_id=offer_id, idempotency_key=label)

            def worker():
                return AcquisitionWorker(repo, root/'acquisition-staging', ingest,
                    allowed=lambda user: admission.account_allowed(root/'app.db', user),
                    enabled=lambda: admission.instance_enabled(root/'app.db'),
                    execution_allowed=lambda job: admission.job_allowed(root/'app.db', job.id, job.owner_id))

            def due():
                with sqlite3.connect(root/'app.db') as connection:
                    connection.execute('UPDATE acquisition_job SET next_attempt_at=0 WHERE id=?', (parent.id,))

            if accepted_recovery:
                assert recovery and not discover
                claim = repo.claim()
                repo.advance(parent.id, claim.token, 'queued', 'resolving')
                repo.advance(parent.id, claim.token, 'resolving', 'downloading')
                repo.begin_submission(parent.id, claim.token)
                repo.record_external(parent.id, claim.token, identity)
                key = repo.submission_identity(parent.id, claim.token)[2]
                tag = 'cwng-'+repo.box.display_identity(str([payload['release_key'], download.id, download.revision, key]))
                submitted.append(dict(tags=tag, urls=requested))
                repo.release(parent.id, claim.token, delay_seconds=0)
            seeded_fence = fence() if accepted_recovery else None
            if accepted_recovery:
                metadata = {'pending-restart':'pending', 'missing-full-restart':'missing',
                            'unavailable-restart':'unavailable', 'same-short-different-full':'mismatch'}[shape]
            current = worker().run_once()
            if shape in ('lt1-retry', 'unknown-retry', 'old-api-retry', 'transmission-refusal'):
                assert current.state == 'failed' and current.error_code == 'unsupported_client_version', current
                assert fence() == (None, None, None, None) and not submitted and not adds, fence()
                assert not list(ingest.iterdir()) and repo.get_receipt(owner, parent.id) is None
                refusals.append(dict(shape=shape, adapter=adapter, engine=engine, error_code=current.error_code,
                                     torrent_adds=0, fence=list(fence())))
                if adapter == 'transmission':
                    continue
                engine = '2.0.11.0'
                api_version = '2.11.2'
                repo.retry(owner, parent.id)
                current = worker().run_once()
            if collision:
                assert current.state == 'failed' and current.error_code == 'torrent_already_exists', current
                assert fence() == (None, None, None, None) and not submitted and not adds and unchanged(), fence()
                collisions.append(dict(shape=shape, torrent_adds=0, fence=list(fence()), source_unchanged=True))
                collision = False  # modeled external removal; CWNG never removes it
                repo.retry(owner, parent.id)
                current = worker().run_once()
            if recovery:
                before = fence()
                if accepted_recovery:
                    assert before == seeded_fence, 'Fresh worker altered the pre-existing accepted fence'
                else:
                    assert before[1:] == tuple(adds[0]['fence_at_add'][1:]), 'Metadata polling altered the wire-time accepted fence'
                books_before = count_books(library)
                assert current.state in (('downloading', 'failed') if accepted_recovery else ('downloading',)) and before[0] == identity and before[1] and before[2], ('Metadata unavailable must not publish', current, before)
                if accepted_recovery and metadata == 'mismatch':
                    assert current.state == 'failed' and current.error_code == 'client_job_mismatch', current
                if current.state == 'failed':
                    repo.retry(owner, parent.id)
                assert not list(ingest.iterdir()) and repo.get_receipt(owner, parent.id) is None
                metadata = {'pending-restart':'pending', 'missing-full-restart':'missing',
                            'unavailable-restart':'unavailable', 'same-short-different-full':'mismatch'}[shape]
                for _ in range(2):
                    due()
                    current = worker().run_once()
                    assert current.state in ('downloading', 'failed') and not list(ingest.iterdir()), ('Unverified full v2 metadata reached publication', metadata, current)
                    if metadata == 'mismatch':
                        assert current.state == 'failed' and current.error_code == 'client_job_mismatch', current
                    assert fence() == before and count_books(library) == books_before and repo.get_receipt(owner, parent.id) is None, fence()
                    assert unchanged() and len(adds) == (0 if accepted_recovery else 1)
                    if current.state == 'failed':
                        repo.retry(owner, parent.id)
                metadata = 'verified'
                due()
                current = worker().run_once()
                assert fence() == before, 'Accepted metadata fence changed during recovery'
            if shape == 'lost-ack-tag-recovery':
                before = fence()
                assert before[0] is None and before[1] and before[2] and len(adds) == 1, before
                assert not list(ingest.iterdir()) and current.state in ('failed', 'downloading')
                if current.state == 'failed':
                    repo.retry(owner, parent.id)
                due()
                current = worker().run_once()
                assert fence()[0] == identity and fence()[1:] == before[1:] and len(adds) == 1
            assert current.state == ('awaiting_selection' if multi else 'importing'), ('Original pure-v2 magnet did not reach normal ingest', current)
            assert any(event == dict(kind='properties', metadata='verified') for event in events), events
            assert fence()[0] == identity
            manifest = None
            if multi:
                response = api.get('/api/v1/acquisition/jobs/'+parent.id+'/books')
                assert response.status_code == 200, response.get_data(as_text=True)
                manifest = response.get_json()
                assert len(manifest['candidates']) == 2 and 'relative_path' not in json.dumps(manifest)
                assert other_api.get('/api/v1/acquisition/jobs/'+parent.id+'/books').status_code == 404
                assert worker().run_once() is None and not list(ingest.iterdir())
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
                with sqlite3.connect(root/'app.db') as connection:
                    connection.execute('UPDATE acquisition_job SET next_attempt_at=0 WHERE id=?', (job.id,))
                assert worker().run_once().state == 'importing' and len(adds) == (0 if accepted_recovery else 1)
                fault = shape == 'single'
                if fault:
                    with sqlite3.connect(root/'app.db') as connection:
                        connection.execute("CREATE TRIGGER v2_magnet_receipt_fault BEFORE INSERT ON acquisition_import_receipt BEGIN SELECT RAISE(ABORT,'v2 magnet receipt fault'); END")

                def process():
                    result = subprocess.run([sys.executable, str(APP/'scripts/ingest_processor.py'), str(published)],
                                            text=True, capture_output=True, timeout=180)
                    print('V2_MAGNET PROCESS '+label+' '+name+' EXIT '+str(result.returncode)+'\n'+result.stdout+result.stderr, flush=True)
                    return result

                result = process()
                if fault:
                    assert result.returncode == 1 and published.is_file() and Path(str(published)+'.cwa.json').is_file()
                    assert repo.get_receipt(owner, job.id) is None
                    after_add = count_books(library)
                    with sqlite3.connect(root/'app.db') as connection:
                        connection.execute('DROP TRIGGER v2_magnet_receipt_fault')
                        connection.execute('UPDATE settings SET config_acquisition_enabled=0')
                    result = process()
                    assert 'Content already imported; skipping duplicate add:' in result.stdout and count_books(library) == after_add
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
                    raise AssertionError('Another owner read a v2 magnet receipt')
                with sqlite3.connect(root/'app.db') as connection:
                    assert connection.execute('SELECT 1 FROM user_library_book WHERE user_id=? AND book_id=?', (owner, receipt.book_ids[0])).fetchone()
                worker().cleanup_completed()
                assert not published.exists() and not Path(str(published)+'.cwa.json').exists()
                assert not (root/'acquisition-staging'/job.id).exists()
                receipts.append(dict(book_ids=list(receipt.book_ids), actual_format='EPUB',
                                     source_sha256=receipt.source_sha256, imported_sha256=receipt.imported_sha256))
            assert len(adds) == (0 if accepted_recovery else 1) and len(submitted) == 1
            assert '/descriptor' not in methods and unchanged() and not server_errors, (methods, server_errors)
            if not accepted_recovery:
                assert methods.index('/api/v2/app/buildInfo') < methods.index('/api/v2/torrents/add')
            assert worker().run_once() is None
            outcomes.append(dict(shape=shape, original_uri=requested, v1_infohash=None, v2_infohash=full,
                external_id=identity, descriptor_sha256=hashlib.sha256(raw).hexdigest(), fixture_evidence=str(evidence),
                piece_layer_count=hashes['piece_layer_count'], remote_submissions=len(adds), receipts=receipts,
                exact_original_uri_url_form=True, descriptor_gets=0, full_metadata_before_publication=True,
                fresh_worker_reused_submission=True, accepted_fence_retained=recovery or shape == 'lost-ack-tag-recovery',
                receipt_fault_recovery=shape == 'single', source_files_and_modes_unchanged=True,
                private_receipts=True, owned_cleanup=True, mixed_bundle_explicit_selection=multi,
                synthetic_padding_excluded_from_choices=multi and len(manifest['candidates']) == 2,
                synthetic_padding_reported=bool(pads), seeding_controls_unchanged=True))
        finally:
            (evidence/'wire.json').write_text(json.dumps(dict(methods=methods, adds=adds, events=events,
                server_errors=server_errors, final_fence=fence() if parent else None), indent=2))
            server.shutdown()
            server.server_close()
            thread.join()
    return dict(outcomes=outcomes, refusals=refusals, collision_retries=collisions,
                real_loopback_http=True, production_catalog_and_choice_get_routes=discover,
                public_source_probe_rejects_loopback=discover,
                native_writes='production admission functions; no s6 HTTP POST availability claim',
                full_processor_subprocess=True, peer_download_or_running_released_client=False)
