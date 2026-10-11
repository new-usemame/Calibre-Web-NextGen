# SPDX-License-Identifier: GPL-3.0-or-later
"""Original legal direct MOBI through the real worker and normal processor."""
import json
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from acquisition_calibre_runtime_probe import APP, count_books, digest, library_format


def run_mobi_runtime(root, repo, owner, other_owner, ingest, library, *, mobi_fixture, mobi_uncompressed_fixture):
    from cps.services.acquisition.catalog import CatalogService, connection_config
    from cps.services.acquisition.worker import AcquisitionWorker
    from cps.services.acquisition.storage import NotFound
    from cwa_db import CWA_DB

    source = mobi_fixture
    source_hash = digest(source)
    originals = {path: digest(path) for path in (source, mobi_uncompressed_fixture)}
    gets = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            gets.append(self.path)
            if self.path == '/catalog':
                body = json.dumps({'metadata': {'title': 'Original legal MOBI'}, 'publications': [
                    {'metadata': {'title': 'Original direct MOBI'}, 'links': [
                        {'rel': 'http://opds-spec.org/acquisition/open-access', 'href': '/book',
                         'type': 'application/x-mobipocket-ebook'}]}]}).encode()
                media = 'application/opds+json'
            elif self.path == '/book':
                body = source.read_bytes()
                media = 'application/x-mobipocket-ebook'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', media)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        origin = 'http://127.0.0.1:' + str(server.server_port)
        cfg = connection_config(dict(endpoint=origin + '/catalog', allow_mobi=True))
        cfg.update(private_origins=[origin], private_networks=['127.0.0.0/8'])
        connection = repo.create_connection('Original legal direct MOBI', 'opds', cfg, enabled=True)
        service = CatalogService(repo)
        worker = AcquisitionWorker(repo, root / 'mobi-staging', ingest,
                                   allowed=lambda value: value in (owner, other_owner))
        results = []
        first_count = count_books(library)
        for conversion, request_owner, uncompressed, expected in (
            (True, owner, False, 'EPUB'), (False, owner, True, 'MOBI'),
            (False, other_owner, True, 'MOBI'), (False, owner, False, 'EPUB'),
        ):
            source = mobi_uncompressed_fixture if uncompressed else mobi_fixture
            source_hash = digest(source)
            db = CWA_DB()
            db.update_cwa_settings(dict(auto_convert=int(conversion), auto_convert_target_format='epub', kindle_epub_fixer=0))
            db.con.close()
            offer = service.browse(request_owner, connection.id)['publications'][0]['offers'][0]
            assert offer['format'] == 'MOBI'
            job = service.request(request_owner, connection.id, offer['offer_id'],
                                  'mobi-' + str(conversion) + '-' + str(request_owner) + '-' + str(uncompressed), requires_approval=False)
            try:
                repo.get_job(other_owner if request_owner == owner else owner, job.id)
            except NotFound:
                pass
            else:
                raise AssertionError('MOBI job crossed its owner boundary')
            worker.run_once()
            assert repo.get_job(request_owner, job.id).state == 'importing'
            published = next(ingest.glob('*.mobi'))
            sidecar = Path(str(published) + '.cwa.json')
            assert digest(published) == source_hash and sidecar.is_file()
            if conversion:
                with sqlite3.connect(root / 'app.db') as conn:
                    conn.execute("CREATE TRIGGER mobi_receipt_fault BEFORE INSERT ON acquisition_import_receipt BEGIN SELECT RAISE(ABORT,'original MOBI receipt fault'); END")

            def process():
                run = subprocess.run([sys.executable, str(APP / 'scripts/ingest_processor.py'), str(published)],
                                     capture_output=True, text=True, timeout=180)
                print('MOBI_PROCESSOR', run.returncode, run.stdout, run.stderr, flush=True)
                return run

            result = process()
            if conversion:
                assert result.returncode == 1 and digest(published) == source_hash and sidecar.is_file()
                after_import = count_books(library)
                with sqlite3.connect(root / 'app.db') as conn:
                    assert conn.execute('SELECT count(*) FROM acquisition_import_receipt WHERE job_id=?', (job.id,)).fetchone()[0] == 0
                    conn.execute('DROP TRIGGER mobi_receipt_fault')
                result = process()
                assert 'Content already imported; skipping duplicate add:' in result.stdout
                assert count_books(library) == after_import
            assert result.returncode == 0 and not published.exists() and not sidecar.exists()
            assert repo.get_job(request_owner, job.id).state == 'imported'
            with sqlite3.connect(root / 'app.db') as conn:
                receipt = conn.execute('SELECT source_sha256,imported_sha256,book_ids_json FROM acquisition_import_receipt WHERE job_id=?', (job.id,)).fetchone()
                book_id = json.loads(receipt[2])[0]
                assert conn.execute('SELECT count(*) FROM user_library_book WHERE user_id=? AND book_id=?', (request_owner, book_id)).fetchone()[0] == 1
            stored = library_format(library, book_id, expected)
            assert stored.is_file() and receipt[:2] == (source_hash, digest(stored))
            if expected == 'MOBI':
                assert digest(stored) == source_hash
            worker.cleanup_completed()
            assert not (root / 'mobi-staging' / job.id).exists()
            results.append(dict(conversion=conversion, owner=request_owner, job_id=job.id,
                                book_id=book_id, actual_format=expected,
                                source_sha256=receipt[0], imported_sha256=receipt[1]))
        assert results[0]['book_id'] != results[1]['book_id']
        assert results[1]['book_id'] == results[2]['book_id']
        assert results[0]['book_id'] == results[3]['book_id']
        assert count_books(library) == first_count + 2
        assert all(digest(path) == value for path, value in originals.items()) and gets.count('/book') == 4
        return dict(results=results, receipt_fault_recovery=True, owned_cleanup=True,
                    cross_owner_refused=True, independent_owner_receipts=True,
                    no_cross_format_metadata_overwrite=True, full_processor_subprocess=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
