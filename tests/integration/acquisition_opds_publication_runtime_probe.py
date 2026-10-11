# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit localized OPDS detail read through child HTTP and normal ingest."""
import json
import sqlite3
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from acquisition_calibre_runtime_probe import APP, digest, ebook, library_format


def run_opds_publication_runtime(root, repo, owner, other_owner, fixture, ingest, library, *, protocol="opds2"):
    from cps.services.acquisition.catalog import CatalogService, connection_config
    from cps.services.acquisition.storage import NotFound
    from cps.services.acquisition.worker import AcquisitionWorker
    from cwa_db import CWA_DB

    assert protocol in ('opds1', 'opds2')
    edition_title = 'Original OPDS1 entry edition' if protocol == 'opds1' else 'Original OPDS detail edition'
    display_title = edition_title if protocol == 'opds1' else 'Édition OPDS originale'
    display_author = 'Original Writer' if protocol == 'opds1' else 'Autrice originale'
    original = ebook(fixture, root/('opds-detail-original-'+protocol+'.epub'), title=edition_title,
                     body='Original legal OPDS detail fixture. Display locale does not change the edition.')
    source_hash = digest(original)
    db = CWA_DB(); db.update_cwa_settings(dict(auto_convert=1, auto_convert_target_format='epub', kindle_epub_fixer=0)); db.con.close()
    gets = []
    metadata = dict(title={'en': 'Original OPDS detail edition', 'fr': 'Édition OPDS originale'},
                    author={'name': {'en': 'Original Writer', 'fr': 'Autrice originale'}},
                    identifier='urn:original:opds-runtime', language='en')
    publication_type = 'application/opds-publication+json'

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            gets.append(self.path)
            if protocol == 'opds1' and self.path in ('/catalog', '/detail?token=DETAIL_SECRET'):
                partial = self.path == '/catalog'
                links = ('<link rel="alternate" href="/detail?token=DETAIL_SECRET" type="application/atom+xml;type=entry;profile=opds-catalog"/>'
                         '<link rel="http://opds-spec.org/acquisition/borrow" href="/loan" type="application/atom+xml"/>') if partial else (
                         '<link rel="self" href="/detail" type="application/atom+xml;type=entry;profile=opds-catalog"/>'
                         '<link rel="http://opds-spec.org/acquisition/open-access" href="original.epub" type="application/epub+zip"/>')
                entry = ('<entry xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/terms/">'
                         '<id>urn:original:opds1-runtime</id><updated>2026-10-03T00:00:00Z</updated><title>Original OPDS1 entry edition</title>'
                         '<author><name>Original Writer</name></author><dc:language>en</dc:language>'+links+'</entry>')
                body = (('<feed xmlns="http://www.w3.org/2005/Atom"><id>urn:original:catalog</id><updated>2026-10-03T00:00:00Z</updated><title>Original Books</title>'+entry+'</feed>') if partial else entry).encode()
                media = 'application/atom+xml;profile=opds-catalog;kind=acquisition' if partial else 'application/atom+xml;type=entry;profile=opds-catalog'
            elif self.path == '/catalog':
                body = json.dumps(dict(metadata=dict(title={'en': 'Original books', 'fr': 'Livres originaux'}),
                    publications=[dict(metadata=metadata, links=[
                        dict(rel='self', href='/detail?token=DETAIL_SECRET', type=publication_type),
                        dict(rel='borrow', href='/loan', type=publication_type)])])).encode()
                media = 'application/opds+json'
            elif self.path == '/detail?token=DETAIL_SECRET':
                body = json.dumps(dict(metadata=metadata, links=[
                    dict(rel='self', href='/detail', type=publication_type),
                    dict(rel='http://opds-spec.org/acquisition/open-access', href='/original.epub', type='application/epub+zip')])).encode()
                media = publication_type
            elif self.path == '/original.epub':
                body = original.read_bytes(); media = 'application/epub+zip'
            else:
                self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type', media)
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self, *args): pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever); thread.start()
    origin = 'http://127.0.0.1:' + str(server.server_port)
    try:
        config = connection_config(dict(endpoint=origin+'/catalog'))
        # Only the isolated test harness can authorize its owned loopback server.
        config.update(private_origins=[origin], private_networks=['127.0.0.0/8'])
        connection = repo.create_connection('Owned OPDS details', 'opds', config, enabled=True)
        service = CatalogService(repo, preferred_language='fr_CA')
        page = service.browse(owner, connection.id)
        book, = page['publications']; detail, = book['navigation']
        assert page['protocol'] == protocol
        assert book['title'] == display_title and book['authors'] == [display_author]
        assert book['languages'] == ['en'] and not book['offers'] and gets == ['/catalog']
        assert 'DETAIL_SECRET' not in json.dumps(page) and origin not in json.dumps(page)
        try: service.browse(other_owner, connection.id, selection=detail['selection'])
        except NotFound: pass
        else: raise AssertionError('detail selection crossed accounts')
        assert gets == ['/catalog']
        page = service.browse(owner, connection.id, selection=detail['selection'])
        book, = page['publications']; offer, = book['offers']
        assert not book['navigation'] and gets == ['/catalog', '/detail?token=DETAIL_SECRET']
        job = service.request(owner, connection.id, offer['offer_id'], 'owned-opds-detail-'+protocol, requires_approval=False)
        assert job.title == display_title
        worker = AcquisitionWorker(repo, root/'acquisition-staging', ingest, allowed=lambda user_id: user_id == owner)
        worker.run_once(); assert repo.get_job(owner, job.id).state == 'importing'
        published = next(ingest.glob('*.epub')); assert digest(published) == source_hash
        result = subprocess.run(['python3', str(APP/'scripts/ingest_processor.py'), str(published)],
                                capture_output=True, text=True, timeout=180)
        print('OPDS DETAIL PROCESS EXIT', result.returncode, result.stdout, result.stderr, flush=True)
        assert result.returncode == 0 and repo.get_job(owner, job.id).state == 'imported'
        with sqlite3.connect(root/'app.db') as sql:
            receipt = sql.execute('SELECT source_sha256,imported_sha256,book_ids_json FROM acquisition_import_receipt WHERE job_id=?', (job.id,)).fetchone()
            ids = json.loads(receipt[2]); assert receipt[0] == source_hash
            assert sql.execute('SELECT user_id FROM user_library_book WHERE book_id=?', (ids[0],)).fetchall() == [(owner,)]
        with sqlite3.connect(library/'metadata.db') as sql:
            assert sql.execute('SELECT title FROM books WHERE id=?', (ids[0],)).fetchone()[0] == edition_title
        assert digest(library_format(library, ids[0])) == receipt[1]
        assert digest(original) == source_hash and not published.exists()
        worker.cleanup_completed(); assert not (root/'acquisition-staging'/job.id).exists()
        assert gets == ['/catalog', '/detail?token=DETAIL_SECRET', '/original.epub']
        return dict(protocol=page['protocol'], saved_display_locale='fr_CA', displayed_title=job.title, edition_language='en',
                    source_sha256=source_hash, imported_sha256=receipt[1], book_ids=ids,
                    explicit_detail_read=True, cross_account_read_refused=True, no_loan_or_purchase_get=True,
                    original_edition_preserved=True, source_unchanged=True, private_cleanup=True,
                    http_gets=gets, full_processor_subprocess=True)
    finally:
        server.shutdown(); server.server_close(); thread.join()
