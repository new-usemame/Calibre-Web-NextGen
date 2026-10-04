#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Owned loopback OPDS through production transfer and full ingest subprocess."""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from acquisition_calibre_runtime_probe import ebook, digest, library_format, annotations, count_books, APP


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fixture", type=Path, required=True)
    p.add_argument("--public-fixture", type=Path)
    p.add_argument("--mobi-fixture", type=Path, default=APP / 'tests/fixtures/sample_books/test_original_direct.mobi')
    p.add_argument("--mobi-uncompressed-fixture", type=Path, default=APP / 'tests/fixtures/sample_books/test_original_direct_uncompressed.mobi')
    args = p.parse_args()
    with tempfile.TemporaryDirectory(prefix="cwng-full-acquisition-") as temp:
        root = Path(temp)
        library = root / "library"
        library.mkdir()
        ingest = root / "ingest"
        ingest.mkdir()
        conversion = root / "conversion"
        conversion.mkdir()
        os.environ.update(
            QTWEBENGINE_DISABLE_SANDBOX="1",
            CWA_APP_DB_PATH=str(root / "app.db"),
            CWA_DB_PATH=str(root),
            CALIBRE_DBPATH=str(root),
            CWA_DIRS_JSON=str(root / "dirs.json"),
        )
        (root / "dirs.json").write_text(
            json.dumps(
                dict(
                    ingest_folder=str(ingest) + "/",
                    calibre_library_dir=str(library) + "/",
                    tmp_conversion_dir=str(conversion) + "/",
                )
            )
        )
        sys.path[:0] = [str(APP), str(APP / "scripts")]
        sys.argv = [sys.argv[0]]
        from cps import ub, config_sql, constants
        from cps.services.acquisition.runtime import open_repository
        from cps.services.acquisition.catalog import CatalogService, connection_config
        from cps.services.acquisition.worker import AcquisitionWorker
        from cwa_db import CWA_DB

        ub.init_db(str(root / "app.db"))
        config_sql._Base.metadata.create_all(ub.session.bind)
        reader = ub.User(
            name="full-reader",
            email="full@example.invalid",
            role=constants.ROLE_ACQUISITION_ACCESS
            | constants.ROLE_ACQUISITION_AUTO_APPROVE,
            has_own_library=True,
        )
        other = ub.User(
            name="other-reader", email="other@example.invalid", has_own_library=True
        )
        ub.session.add_all(
            [
                reader,
                other,
                config_sql._Settings(
                    config_calibre_dir=str(library), config_acquisition_enabled=True
                ),
            ]
        )
        ub.session.commit()
        owner = reader.id
        other_owner = other.id
        ub.session.close()
        ub.session.bind.dispose()
        db = CWA_DB()
        db.update_cwa_settings(
            dict(
                auto_backup_imports=0,
                auto_backup_conversions=0,
                auto_backup_epub_fixes=0,
                auto_ingest_automerge="overwrite",
                auto_metadata_enforcement=0,
            )
        )
        db.con.close()
        subprocess.run(
            [
                "calibre-debug",
                "-c",
                "from calibre.db.legacy import LibraryDatabase; d=LibraryDatabase("
                + repr(str(library))
                + "); d.close()",
            ],
            check=True,
        )
        files = {}
        for name, title in [
            ("patch", "Patched runtime EPUB"),
            ("duplicate", "Patched runtime EPUB"),
            ("convert", "Converted runtime KEPUB"),
            ("pdf", "Direct runtime PDF"),
        ]:
            files["/" + name + ".epub"] = ebook(
                args.fixture,
                root / (name + ".epub"),
                title=title,
                body="Owned "
                + name
                + " source with enough text to test actual processing.",
            )
        pdf = root / "direct.pdf"
        r = subprocess.run(
            ["ebook-convert", str(files["/pdf.epub"]), str(pdf)],
            capture_output=True,
            text=True,
            timeout=150,
        )
        print(r.stdout, r.stderr, flush=True)
        assert r.returncode == 0
        files["/direct.pdf"] = pdf
        files["/repacked.epub"] = root / "repacked.epub"
        files["/container.epub"] = root / "container.epub"
        if args.public_fixture:
            files["/public.epub"] = args.public_fixture
        gets = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                gets.append(self.path)
                if self.path == "/catalog":
                    body = json.dumps(
                        {
                            "metadata": {"title": "Owned runtime catalog"},
                            "publications": [
                                {
                                    "metadata": {"title": name},
                                    "links": [
                                        {
                                            "rel": "http://opds-spec.org/acquisition",
                                            "href": name,
                                            "type": (
                                                "application/pdf"
                                                if name.endswith(".pdf")
                                                else "application/epub+zip"
                                            ),
                                        }
                                    ],
                                }
                                for name in files
                                if name != "/pdf.epub"
                            ],
                        }
                    ).encode()
                    media = "application/opds+json"
                elif self.path in files:
                    body = files[self.path].read_bytes()
                    media = (
                        "application/pdf"
                        if self.path.endswith(".pdf")
                        else "application/epub+zip"
                    )
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", media)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        origin = "http://127.0.0.1:" + str(server.server_port)
        results = []
        try:
            with open_repository(root / "app.db", initialize_key=True) as repo:
                # This probe's catalog is a throwaway HTTP server bound to
                # loopback, which no administrator is allowed to configure:
                # `connection_config` refuses 127.0.0.0/8 so a whitelist entry
                # cannot be used to walk past advocate's loopback rule.
                # Still go through that validation for everything else it
                # checks, then widen the one field the harness needs -- rather
                # than hand-building the config and testing nothing.
                material = connection_config(dict(endpoint=origin + "/catalog"))
                assert material["private_networks"] == []
                material["private_origins"] = [origin]
                material["private_networks"] = ["127.0.0.0/8"]
                connection = repo.create_connection(
                    "Owned local HTTP", "opds", material, enabled=True,
                )
                service = CatalogService(repo)
                page = service.browse(owner, connection.id)
                assert gets == ["/catalog"]
                offers = {
                    pub["title"]: pub["offers"][0]["offer_id"]
                    for pub in page["publications"]
                }
                worker = AcquisitionWorker(
                    repo,
                    root / "acquisition-staging",
                    ingest,
                    allowed=lambda user_id: user_id in (owner, other_owner),
                )
                first_hash = None
                first_id = None
                first_job_id = None
                reader_state = None
                calibre_annotations = None
                cases = [
                    ("patch", "epub", 1, True),
                    ("duplicate", "epub", 1, False),
                    ("convert", "kepub", 0, False),
                    ("direct", "pdf", 0, False),
                    ("repacked", "epub", 0, True),
                    ("container", "epub", 0, True),
                ]
                if args.public_fixture:
                    cases.append(("public", "epub", 1, False))
                for name, target, fixer, fail_receipt in cases:
                    suffix = "pdf" if name == "direct" else "epub"
                    url = "/" + name + "." + suffix
                    if name in ("repacked", "container"):
                        with zipfile.ZipFile(library_format(library, first_id)) as original, zipfile.ZipFile(files[url], "w") as repacked:
                            names = ["mimetype"] + sorted((item for item in original.namelist() if item != "mimetype"), reverse=True)
                            for member in names:
                                info = zipfile.ZipInfo(member, (2026, 10, 3, 0, 0, 0))
                                info.compress_type = zipfile.ZIP_STORED if member == "mimetype" else zipfile.ZIP_DEFLATED
                                payload = original.read(member)
                                if name == "container" and member == "META-INF/container.xml":
                                    # Legal original locator; change serialization, not its attributes.
                                    tree = ET.fromstring(payload)
                                    ET.indent(tree)
                                    ET.register_namespace("ocf", "urn:oasis:names:tc:opendocument:xmlns:container")
                                    payload = ET.tostring(tree, encoding="utf-8", xml_declaration=True)
                                    assert payload != original.read(member)
                                repacked.writestr(info, payload)
                            repacked.comment = b"Same retained resources; new ZIP packaging."
                        with zipfile.ZipFile(library_format(library, first_id)) as before, zipfile.ZipFile(files[url]) as after:
                            assert set(before.namelist()) == set(after.namelist())
                            changed_members = [member for member in before.namelist() if before.read(member) != after.read(member)]
                            assert changed_members == (["META-INF/container.xml"] if name == "container" else [])
                        assert digest(files[url]) != first_hash

                    db = CWA_DB()
                    db.update_cwa_settings(
                        dict(
                            auto_convert=1,
                            auto_convert_target_format=target,
                            kindle_epub_fixer=fixer,
                        )
                    )
                    db.con.close()
                    job = service.request(
                        owner,
                        connection.id,
                        offers[url],
                        "full-" + name,
                        requires_approval=False,
                    )
                    worker.run_once()
                    current = repo.get_job(owner, job.id)
                    assert current.state == "importing", current
                    published = next(ingest.glob("*." + suffix))
                    sidecar = Path(str(published) + ".cwa.json")
                    source_hash = digest(files[url])
                    assert digest(published) == source_hash
                    if name == "duplicate":
                        # The old version could persist a title/author-only
                        # retention result. Exercise both processor and helper
                        # recovery paths without changing the historical receipt.
                        legacy = dict(source_sha256=source_hash,
                                      imported_sha256=first_hash, book_ids=[first_id],
                                      disposition="existing_retained", format="epub")
                        with sqlite3.connect(library / "metadata.db") as c:
                            c.execute("INSERT INTO cwng_acquisition_ingest_result VALUES (?,?)",
                                      (source_hash, json.dumps(legacy)))
                    if fail_receipt:
                        with sqlite3.connect(root / "app.db") as c:
                            prior_memberships = c.execute("SELECT count(*) FROM user_library_book").fetchone()[0]
                            prior_receipts = c.execute("SELECT count(*) FROM acquisition_import_receipt").fetchone()[0]
                            c.execute(
                                "CREATE TRIGGER reject_receipt BEFORE INSERT ON acquisition_import_receipt BEGIN SELECT RAISE(ABORT,'owned receipt fault'); END"
                            )

                    def process():
                        r = subprocess.run(
                            [
                                "python3",
                                str(APP / "scripts/ingest_processor.py"),
                                str(published),
                            ],
                            capture_output=True,
                            text=True,
                            timeout=180,
                        )
                        print(
                            "\nPROCESS "
                            + name
                            + " EXIT "
                            + str(r.returncode)
                            + "\n"
                            + r.stdout
                            + r.stderr,
                            flush=True,
                        )
                        return r

                    r = process()
                    if fail_receipt:
                        assert (
                            r.returncode == 1
                            and published.is_file()
                            and sidecar.is_file()
                        ), r.returncode
                        assert digest(published) == source_hash
                        assert (root / "acquisition-staging" / job.id).is_dir()
                        with sqlite3.connect(root / "app.db") as c:
                            assert (
                                c.execute(
                                    "select count(*) from user_library_book"
                                ).fetchone()[0]
                                == prior_memberships
                            )
                            assert (
                                c.execute(
                                    "select count(*) from acquisition_import_receipt"
                                ).fetchone()[0]
                                == prior_receipts
                            )
                            c.execute("DROP TRIGGER reject_receipt")
                            c.execute(
                                "update settings set config_acquisition_enabled=0"
                            )
                        r = process()
                        assert (
                            "Content already imported; skipping duplicate add:"
                            in r.stdout
                        )
                        with sqlite3.connect(root / "app.db") as c:
                            c.execute(
                                "update settings set config_acquisition_enabled=1"
                            )
                    assert r.returncode == 0, r.returncode
                    assert not published.exists() and not sidecar.exists()
                    assert repo.get_job(owner, job.id).state == "imported"
                    with sqlite3.connect(root / "app.db") as c:
                        receipt = c.execute(
                            "select source_sha256,imported_sha256,book_ids_json from acquisition_import_receipt where job_id=?",
                            (job.id,),
                        ).fetchone()
                        ids = json.loads(receipt[2])
                        assert receipt[0] == source_hash
                        memberships = c.execute(
                            "select user_id,book_id from user_library_book"
                        ).fetchall()
                        assert all(row[0] == owner for row in memberships)
                        assert (owner, ids[0]) in memberships
                        assert (
                            len(memberships)
                            == {
                                "patch": 1,
                                "duplicate": 2,
                                "convert": 3,
                                "direct": 4,
                                "repacked": 4,
                                "container": 4,
                                "public": 5,
                            }[name]
                        )
                    stored = library_format(library, ids[0], target.upper())
                    assert stored and stored.exists()
                    assert digest(stored) == receipt[1]
                    if name == "patch":
                        first_hash = receipt[1]
                        first_id = ids[0]
                        first_job_id = job.id
                        calibre_annotations = annotations(library, first_id, seed=True)
                        assert first_hash != source_hash, "patch not exercised"
                        ub.session.add_all([
                            ub.Annotation(user_id=owner, book_id=first_id,
                                          annotation_id="edition-fixture", source="webreader",
                                          highlighted_text="Original edition passage",
                                          note_text="Keep this on the original edition",
                                          cfi_range="epubcfi(/6/2!/4/2/1:0)"),
                            ub.Bookmark(user_id=owner, book_id=first_id, format="epub",
                                        bookmark_key="epubcfi(/6/2!/4/2/1:0)"),
                            ub.ReadBook(user_id=owner, book_id=first_id,
                                        read_status=ub.ReadBook.STATUS_IN_PROGRESS),
                        ])
                        ub.session.commit()
                        ub.session.close()
                        # Establish the same migrated annotation/device state
                        # an existing reader has before the next acquisition.
                        ub.init_db(str(root / "app.db"))
                        ub.session.close()
                        with sqlite3.connect(root / "app.db") as c:
                            reader_state = {table: c.execute(
                                "SELECT * FROM " + table + " WHERE user_id=? AND book_id=?",
                                (owner, first_id),
                            ).fetchall() for table in ("annotation", "bookmark", "book_read_link")}
                        assert all(len(rows) == 1 for rows in reader_state.values())
                    if name == "duplicate":
                        assert ids != [first_id] and receipt[1] != first_hash
                        assert digest(library_format(library, first_id)) == first_hash
                        with sqlite3.connect(root / "app.db") as c:
                            historical = c.execute(
                                "SELECT imported_sha256,book_ids_json FROM acquisition_import_receipt WHERE job_id=?",
                                (first_job_id,),
                            ).fetchone()
                        assert historical[0] == first_hash and json.loads(historical[1]) == [first_id]
                        with sqlite3.connect(root / "app.db") as c:
                            for table, rows in reader_state.items():
                                current = c.execute("SELECT * FROM " + table + " WHERE user_id=? AND book_id=?",
                                                    (owner, first_id)).fetchall()
                                assert current == rows, (table, rows, current)
                                assert c.execute("SELECT count(*) FROM " + table + " WHERE book_id=?",
                                                 (ids[0],)).fetchone()[0] == 0
                        with zipfile.ZipFile(stored) as book:
                            text = b"".join(book.read(path) for path in book.namelist()
                                            if path.endswith((".xhtml", ".html")))
                        assert b"Owned duplicate source" in text
                    if name in ("repacked", "container"):
                        assert ids == [first_id] and receipt[1] == first_hash
                        assert count_books(library) == 4
                        assert annotations(library, first_id) == calibre_annotations
                        with sqlite3.connect(library / "metadata.db") as c:
                            provenance = json.loads(c.execute("SELECT result_json FROM cwng_acquisition_ingest_result WHERE source_sha256=?", (source_hash,)).fetchone()[0])
                        assert provenance["artifact_identity_version"] == (3 if name == "container" else 2) and provenance["disposition"] == "existing_retained"
                        with sqlite3.connect(root / "app.db") as c:
                            for table, rows in reader_state.items():
                                assert c.execute("SELECT * FROM " + table + " WHERE user_id=? AND book_id=?", (owner, first_id)).fetchall() == rows
                            historical = c.execute("SELECT imported_sha256,book_ids_json FROM acquisition_import_receipt WHERE job_id=?", (first_job_id,)).fetchone()
                        assert historical[0] == first_hash and json.loads(historical[1]) == [first_id]
                    if name == "convert":
                        assert receipt[1] != source_hash
                    assert gets.count(url) == 1
                    worker.cleanup_completed()
                    assert not (root / "acquisition-staging" / job.id).exists()
                    results.append(
                        dict(
                            case=name,
                            format=target,
                            source_sha256=source_hash,
                            imported_sha256=receipt[1],
                            book_ids=ids,
                            receipt_retry=fail_receipt,
                            source_and_private_cleanup=True,
                        )
                    )
                identities = {}
                for resource_url, identity_version in (("/repacked.epub", 2), ("/container.epub", 3)):
                    other_page = service.browse(other_owner, connection.id)
                    other_offer = next(pub["offers"][0]["offer_id"] for pub in other_page["publications"] if pub["title"] == resource_url)
                    other_job = service.request(other_owner, connection.id, other_offer, "full-other-" + str(identity_version), requires_approval=False, add_to_my_library=True)
                    worker.run_once()
                    assert repo.get_job(other_owner, other_job.id).state == "importing"
                    published = next(ingest.glob("*.epub"))
                    sidecar = Path(str(published) + ".cwa.json")
                    before_other = count_books(library)
                    r = process()
                    assert r.returncode == 0 and not published.exists() and not sidecar.exists()
                    assert repo.get_job(other_owner, other_job.id).state == "imported"
                    with sqlite3.connect(root / "app.db") as c:
                        other_receipt = c.execute("SELECT source_sha256,imported_sha256,book_ids_json FROM acquisition_import_receipt WHERE job_id=?", (other_job.id,)).fetchone()
                        assert c.execute("SELECT user_id,book_id FROM user_library_book WHERE user_id=?", (other_owner,)).fetchall() == [(other_owner, first_id)]
                    assert other_receipt[:2] == (digest(files[resource_url]), first_hash) and json.loads(other_receipt[2]) == [first_id]
                    assert count_books(library) == before_other and annotations(library, first_id) == calibre_annotations
                    worker.cleanup_completed()
                    assert not (root / "acquisition-staging" / other_job.id).exists()
                    identities[identity_version] = dict(retained_book_id=first_id, source_sha256=other_receipt[0], imported_sha256=first_hash,
                                       identity_version=identity_version, resources_preserved=True, original_bytes_and_annotations=True, app_reading_state=True,
                                       receipt_retry_no_reimport=True, other_owner_membership=True, distinct_owned_receipts=True)
                repackaging = dict(identities[2], zip_only_equal=True)
                container_serialization = dict(identities[3], ordinary_locator_only=True)
                from acquisition_opds_publication_runtime_probe import run_opds_publication_runtime
                opds_publication = run_opds_publication_runtime(root,repo,owner,other_owner,args.fixture,ingest,library)
                opds1_entry = run_opds_publication_runtime(root,repo,owner,other_owner,args.fixture,ingest,library,protocol='opds1')
                from acquisition_bundle_runtime_probe import run_bundle_runtime
                bundle = run_bundle_runtime(root,repo,owner,other_owner,args.fixture,ingest,library)
                from acquisition_torrent_metadata_runtime_probe import run_torrent_metadata_runtime
                torrent_metadata = run_torrent_metadata_runtime(root,repo,owner,args.fixture,ingest,library)
                from acquisition_hybrid_runtime_probe import run_hybrid_runtime
                hybrid = run_hybrid_runtime(root,repo,owner,other_owner,args.fixture,ingest,library)
                from acquisition_torrent_v2_runtime_probe import run_torrent_v2_runtime
                pure_v2 = run_torrent_v2_runtime(root,repo,owner,other_owner,args.fixture,ingest,library)
                from acquisition_mobi_runtime_probe import run_mobi_runtime
                mobi = run_mobi_runtime(root,repo,owner,other_owner,ingest,library,
                                        mobi_fixture=args.mobi_fixture, mobi_uncompressed_fixture=args.mobi_uncompressed_fixture)
                from acquisition_client_mobi_runtime_probe import run_client_mobi_runtime
                client_mobi = run_client_mobi_runtime(root,repo,owner,other_owner,args.fixture,ingest,library,
                                                    mobi_fixture=args.mobi_fixture,
                                                    mobi_uncompressed_fixture=args.mobi_uncompressed_fixture)
                assert digest(library_format(library, first_id)) == first_hash
                assert annotations(library, first_id) == calibre_annotations
                with sqlite3.connect(root / "app.db") as c:
                    for table, rows in reader_state.items():
                        assert c.execute("SELECT * FROM " + table + " WHERE user_id=? AND book_id=?",
                            (owner,first_id)).fetchall() == rows
                client_mobi['original_epub_and_reading_state_preserved'] = True
                pure_v2['original_epub_and_reading_state_preserved'] = True
                pure_v2['prior_seed_checks'] = dict(
                    book_id=first_id, stored_sha256=first_hash,
                    calibre_bookmarks=calibre_annotations,
                    app_reading_state=reader_state,
                )
            print(
                "CWNG_ACQUISITION_FULL_RUNTIME="
                + json.dumps(
                    dict(
                        results=results,
                        bundle=bundle,
                        repackaging=repackaging,
                        container_serialization=container_serialization,
                        opds_publication=opds_publication,
                        opds1_entry=opds1_entry,
                        torrent_metadata=torrent_metadata,
                        hybrid=hybrid,
                        pure_v2=pure_v2,
                        mobi=mobi,
                        client_mobi=client_mobi,
                        http_gets=gets,
                        full_processor_subprocess=True,
                        network="isolated loopback only",
                    ),
                    sort_keys=True,
                ),
                flush=True,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    main()
