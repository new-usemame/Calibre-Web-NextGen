# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""An import that reports no book id fails; it never acts on a guessed book (#2544).

The importer used to fall back to the most recently modified row in
``metadata.db`` when the Calibre helper reported no id for the book it added.
Any other book touched more recently (an edit in the web UI, another ingest)
then received this import's metadata fetch, path fix, generated cover,
auto-send and "Recently Added" timestamp.
"""

import sqlite3
from contextlib import nullcontext
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

UNRELATED_ID = 3
UNRELATED_TIMESTAMP = "2001-01-01 00:00:00+00:00"


class _ImportDb:
    def __init__(self):
        self.entries = []

    def import_add_entry(self, *args):
        self.entries.append(args)


@pytest.fixture
def ingest_processor(monkeypatch, tmp_path):
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    monkeypatch.setenv("CWA_INGEST_BATCH_DIRTY_FILE", str(tmp_path / "batch_dirty"))
    monkeypatch.setenv("CWA_INGEST_BATCH_ACTIVE_FILE", str(tmp_path / "batch_active"))
    import ingest_processor as module
    return module


def _metadata_db(path):
    with sqlite3.connect(path) as con:
        con.execute(
            "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT, "
            "author_sort TEXT, path TEXT, timestamp TEXT, last_modified TEXT)"
        )
        con.execute(
            "INSERT INTO books VALUES (1, 'Older', 'Older', 'A', 'A/Older (1)', "
            "'2000-01-01 00:00:00+00:00', '2000-01-01 00:00:00+00:00')"
        )
        # Edited a moment ago by someone else: the newest last_modified.
        con.execute(
            "INSERT INTO books VALUES (?, 'Unrelated', 'Unrelated', 'B', "
            "'B/Unrelated (3)', ?, '2099-01-01 00:00:00+00:00')",
            (UNRELATED_ID, UNRELATED_TIMESTAMP),
        )


def _processor(module, tmp_path, monkeypatch):
    processor = object.__new__(module.NewBookProcessor)
    processor.target_format = "epub"
    processor.is_kindle_epub_fixer = False
    processor.cwa_settings = {"auto_ingest_automerge": "new_record", "auto_backup_imports": False}
    processor.metadata_db = str(tmp_path / "metadata.db")
    processor.staging_dir = str(tmp_path / "staging")
    processor.tmp_conversion_dir = str(tmp_path / "conversion")
    processor.calibre_env = {}
    processor.library_dir = str(tmp_path / "library")
    processor.last_added_book_id = None
    processor.last_added_book_ids = []
    processor.db = _ImportDb()
    processor.filepath = str(tmp_path / "incoming.epub")
    processor.original_filename = "incoming.epub"
    processor._title_sort_regex = r"^(A|The|An)\s+"
    for path in (processor.staging_dir, processor.tmp_conversion_dir, processor.library_dir):
        Path(path).mkdir(exist_ok=True)
    _metadata_db(processor.metadata_db)

    monkeypatch.setattr(module, "wait_for_duplicate_full_scan_to_finish", lambda: None)
    monkeypatch.setattr(module, "gdrive_sync_if_enabled", lambda: None)
    monkeypatch.setattr(module, "_is_koreader_sync_enabled", lambda: True)
    monkeypatch.setattr(module, "offline_library_access", nullcontext)
    monkeypatch.setattr(module, "metadata_db_write_lock", nullcontext)
    processor._comic_calibredb_metadata_args = lambda *_: []
    processor._content_marker_book_ids = lambda *_: []
    processor._run_calibre_transaction = (
        lambda _staged, _identity, _imported, _source, _metadata, action:
        {"status": "inspect", "formats": []} if action == "inspect"
        else {"status": "imported", "added_ids": [], "updated_ids": [], "book_ids": []}
    )

    # Every consumer that acts on a specific book records which one it got.
    touched = []
    processor.record_original_filename = lambda: touched.append(("original_filename", processor.last_added_book_ids))
    processor.fetch_metadata_if_enabled = lambda *a, **k: touched.append(("metadata", a, k))
    processor._fix_unicode_path = lambda *a: touched.append(("path", a))
    processor.generate_missing_cover_if_enabled = lambda *a: touched.append(("cover", a))
    processor.trigger_auto_send_if_enabled = lambda *a, **k: touched.append(("auto_send", a, k))
    processor.generate_book_checksums = lambda *a, **k: touched.append(("checksums", a, k))
    monkeypatch.setattr(module, "run_duplicate_scan_for_books", lambda ids: touched.append(("duplicates", ids)))
    return processor, touched


def _unrelated_row(processor):
    with sqlite3.connect(processor.metadata_db) as con:
        return con.execute(
            "SELECT title, path, timestamp, last_modified FROM books WHERE id = ?",
            (UNRELATED_ID,),
        ).fetchone()


def test_import_without_a_reported_id_fails_and_leaves_other_books_alone(
    ingest_processor, monkeypatch, tmp_path
):
    processor, touched = _processor(ingest_processor, tmp_path, monkeypatch)
    source = tmp_path / "incoming.epub"
    source.write_bytes(b"a book the helper added without reporting its id")
    before = _unrelated_row(processor)

    with pytest.raises(ingest_processor.PreserveIngestSourceError, match="no book id"):
        processor.add_book_to_library(str(source))

    assert touched == []
    assert processor.db.entries == []
    assert processor.last_added_book_id is None
    assert _unrelated_row(processor) == before
    assert before[2] == UNRELATED_TIMESTAMP


def test_import_with_a_reported_id_still_runs_post_import_work_on_that_book(
    ingest_processor, monkeypatch, tmp_path
):
    """Control: the failure is specific to a missing id, not to every import."""
    processor, touched = _processor(ingest_processor, tmp_path, monkeypatch)
    processor._run_calibre_transaction = (
        lambda _staged, _identity, _imported, _source, _metadata, action:
        {"status": "inspect", "formats": []} if action == "inspect"
        else {"status": "imported", "added_ids": [1], "updated_ids": [], "book_ids": [1]}
    )
    source = tmp_path / "incoming.epub"
    source.write_bytes(b"a book the helper added as id 1")

    processor.add_book_to_library(str(source))

    assert processor.last_added_book_id == 1
    assert ("metadata", (), {"book_id": 1}) in touched
    assert ("cover", (1,)) in touched
    assert _unrelated_row(processor)[2] == UNRELATED_TIMESTAMP


def test_metadata_fetch_and_auto_send_without_an_id_never_pick_the_newest_book(
    ingest_processor, monkeypatch, tmp_path
):
    processor = object.__new__(ingest_processor.NewBookProcessor)
    processor.metadata_db = str(tmp_path / "metadata.db")
    _metadata_db(processor.metadata_db)
    fetched, recipient_lookups = [], []
    monkeypatch.setattr(ingest_processor, "_CPS_AVAILABLE", True)
    monkeypatch.setattr(ingest_processor, "fetch_and_apply_metadata", lambda book_id: fetched.append(book_id))
    monkeypatch.setattr(ingest_processor, "TaskAutoSend", object)
    monkeypatch.setattr(ingest_processor, "WorkerThread", object)
    monkeypatch.setattr(
        ingest_processor, "get_app_db_path",
        lambda: recipient_lookups.append(True) or str(tmp_path / "app.db"),
    )

    processor.fetch_metadata_if_enabled("incoming")
    processor.trigger_auto_send_if_enabled("incoming", str(tmp_path / "incoming.epub"))

    assert fetched == []
    assert recipient_lookups == []
