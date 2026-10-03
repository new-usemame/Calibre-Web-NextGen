# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Behavioral regression coverage for destructive automerge protection."""

import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


class _ImportDb:
    def import_add_entry(self, *_args):
        pass


def _processor(ingest_processor, tmp_path):
    processor = object.__new__(ingest_processor.NewBookProcessor)
    processor.target_format = "epub"
    processor.is_kindle_epub_fixer = False
    processor.cwa_settings = {"auto_ingest_automerge": "overwrite", "auto_backup_imports": False}
    processor.metadata_db = str(tmp_path / "metadata.db")
    processor.staging_dir = str(tmp_path / "staging")
    processor.tmp_conversion_dir = str(tmp_path / "conversion")
    processor.calibre_env = {}
    processor.library_dir = str(tmp_path / "library")
    processor.last_added_book_id = None
    processor.last_added_book_ids = []
    processor.last_added_ids_are_fallback = False
    processor.db = _ImportDb()
    processor.filepath = str(tmp_path / "incoming.epub")
    processor.original_filename = "incoming.epub"
    for path in (processor.staging_dir, processor.tmp_conversion_dir, processor.library_dir):
        Path(path).mkdir(exist_ok=True)
    return processor


def _disable_post_import_work(ingest_processor, processor, monkeypatch):
    monkeypatch.setattr(ingest_processor, "wait_for_duplicate_full_scan_to_finish", lambda: None)
    monkeypatch.setattr(ingest_processor, "gdrive_sync_if_enabled", lambda: None)
    monkeypatch.setattr(ingest_processor, "run_duplicate_scan_for_books", lambda *_: None)
    monkeypatch.setattr(ingest_processor, "_is_koreader_sync_enabled", lambda: False)
    processor.record_original_filename = lambda: None
    processor.fetch_metadata_if_enabled = lambda *args, **kwargs: None
    processor._fix_unicode_path = lambda *_: None
    processor.trigger_auto_send_if_enabled = lambda *args, **kwargs: None
    processor._comic_calibredb_metadata_args = lambda *_: []
    processor._content_marker_book_ids = lambda *_: []


@pytest.fixture
def ingest_processor(monkeypatch, tmp_path):
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    monkeypatch.setenv("CWA_INGEST_BATCH_DIRTY_FILE", str(tmp_path / "batch_dirty"))
    monkeypatch.setenv("CWA_INGEST_BATCH_ACTIVE_FILE", str(tmp_path / "batch_active"))
    import ingest_processor as module
    return module


def _backup_dirs(ingest_processor, monkeypatch, tmp_path):
    failed = tmp_path / "processed_books" / "failed"
    overwritten = tmp_path / "processed_books" / "overwritten"
    failed.mkdir(parents=True)
    overwritten.mkdir(parents=True)
    monkeypatch.setattr(
        ingest_processor, "backup_destinations",
        {"failed": str(failed), "overwritten": str(overwritten)},
    )
    return failed, overwritten


def test_converted_retries_use_the_staged_source_identity(
    ingest_processor, monkeypatch, tmp_path
):
    source = tmp_path / "incoming.txt"
    source.write_bytes(b"same persistent source")
    first_conversion = tmp_path / "first.epub"
    first_conversion.write_bytes(b"epub package generated at time one")
    second_conversion = tmp_path / "second.epub"
    second_conversion.write_bytes(b"different epub package generated at time two")
    processor = _processor(ingest_processor, tmp_path)
    processor.filepath = str(source)
    processor.cwa_settings["auto_ingest_automerge"] = "new_record"
    _disable_post_import_work(ingest_processor, processor, monkeypatch)
    committed_markers = set()
    imports = []

    processor._content_marker_book_ids = (
        lambda digest: [7] if digest in committed_markers else []
    )

    def transaction(
        staged, staged_identity, imported_digest, source_digest, _metadata, action
    ):
        assert action == "import"
        assert ingest_processor._sha256_file(staged) == imported_digest
        assert ingest_processor._sha256_file(staged_identity) == source_digest
        committed_markers.add(source_digest)
        imports.append(staged.read_bytes())
        return {"status": "imported", "book_ids": [7]}

    processor._run_calibre_transaction = transaction
    processor.add_book_to_library(str(first_conversion), identity_path=str(source))
    processor.add_book_to_library(str(second_conversion), identity_path=str(source))

    assert imports == [b"epub package generated at time one"]
    assert committed_markers == {ingest_processor._sha256_file(source)}


def test_nonduplicate_overwrite_skips_sanity_validation(
    ingest_processor, monkeypatch, tmp_path
):
    source = tmp_path / "new-title.epub"
    source.write_bytes(b"new valid title")
    processor = _processor(ingest_processor, tmp_path)
    _disable_post_import_work(ingest_processor, processor, monkeypatch)
    sanity_calls = []

    processor._incoming_file_is_sane = lambda *_args, **_kwargs: sanity_calls.append(True) or False

    def transaction(
        _staged, _staged_identity, _imported_digest, _source_digest, _metadata, action
    ):
        if action == "inspect":
            return {"status": "inspect", "formats": []}
        return {"status": "imported", "book_ids": [7]}

    processor._run_calibre_transaction = transaction
    processor.add_book_to_library(str(source))

    assert sanity_calls == []
    assert processor.last_added_book_id == 7


def test_audiobook_sanity_uses_bounded_mutagen_probe_without_ffprobe(
    ingest_processor, monkeypatch, tmp_path
):
    source = tmp_path / "incoming.m4b"
    source.write_bytes(b"audio container")
    processor = _processor(ingest_processor, tmp_path)
    commands = []

    def run(command, **kwargs):
        commands.append((command, kwargs))
        return ingest_processor.subprocess.CompletedProcess(command, 0, stdout="120.0\n")

    monkeypatch.setattr(ingest_processor.subprocess, "run", run)

    assert processor._incoming_file_is_sane(source, text=False)
    command, kwargs = commands[0]
    assert command[0] == ingest_processor.sys.executable
    assert "mutagen" in command[2]
    assert "ffprobe" not in command
    assert kwargs["timeout"] == 90.0


def test_broken_incoming_duplicate_is_quarantined_before_overwrite(
    ingest_processor, monkeypatch, tmp_path
):
    source = tmp_path / "incoming.epub"
    source.write_bytes(b"broken incoming")
    existing = tmp_path / "library" / "Author" / "Book" / "Book.epub"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"known good existing")
    failed, _overwritten = _backup_dirs(ingest_processor, monkeypatch, tmp_path)
    processor = _processor(ingest_processor, tmp_path)
    _disable_post_import_work(ingest_processor, processor, monkeypatch)
    processor._incoming_file_is_sane = lambda *_args, **_kwargs: False
    transaction_calls = []

    def transaction(_staged, _identity, _imported, _source, _metadata, action):
        transaction_calls.append(action)
        assert action == "inspect"
        return {"formats": [{"book_id": 7, "path": str(existing)}]}

    processor._run_calibre_transaction = transaction

    processor.add_book_to_library(str(source))

    assert transaction_calls == ["inspect"]
    assert existing.read_bytes() == b"known good existing"
    assert [path.read_bytes() for path in failed.iterdir()] == [b"broken incoming"]


def test_sane_overwrite_preserves_previous_format_before_transaction(
    ingest_processor, monkeypatch, tmp_path
):
    source = tmp_path / "incoming.epub"
    source.write_bytes(b"validated replacement")
    existing = tmp_path / "library" / "Author" / "Book" / "Book.epub"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"known good existing")
    _failed, overwritten = _backup_dirs(ingest_processor, monkeypatch, tmp_path)
    processor = _processor(ingest_processor, tmp_path)
    _disable_post_import_work(ingest_processor, processor, monkeypatch)
    processor._incoming_file_is_sane = lambda *_args, **_kwargs: True

    def transaction(staged, _identity, _imported, _source, _metadata, action):
        if action == "inspect":
            return {"formats": [{"book_id": 7, "path": str(existing)}]}
        preserved = [path for path in overwritten.rglob("*") if path.is_file()]
        assert [path.read_bytes() for path in preserved] == [b"known good existing"]
        shutil.copyfile(staged, existing)
        return {"status": "imported", "book_ids": [7]}

    processor._run_calibre_transaction = transaction
    processor.add_book_to_library(str(source))

    assert existing.read_bytes() == b"validated replacement"
    preserved = [path for path in overwritten.rglob("*") if path.is_file()]
    assert [path.read_bytes() for path in preserved] == [b"known good existing"]


def test_failed_database_transaction_restores_preserved_format(
    ingest_processor, monkeypatch, tmp_path
):
    source = tmp_path / "incoming.epub"
    source.write_bytes(b"validated replacement")
    existing = tmp_path / "library" / "Author" / "Book" / "Book.epub"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"known good existing")
    _failed, overwritten = _backup_dirs(ingest_processor, monkeypatch, tmp_path)
    processor = _processor(ingest_processor, tmp_path)
    _disable_post_import_work(ingest_processor, processor, monkeypatch)
    processor._incoming_file_is_sane = lambda *_args, **_kwargs: True

    def transaction(staged, _identity, _imported, _source, _metadata, action):
        if action == "inspect":
            return {"formats": [{"book_id": 7, "path": str(existing)}]}
        shutil.copyfile(staged, existing)
        raise ingest_processor.subprocess.CalledProcessError(1, ["calibre-debug"])

    processor._run_calibre_transaction = transaction

    with pytest.raises(ingest_processor.RetryIngestSourceError):
        processor.add_book_to_library(str(source))

    assert existing.read_bytes() == b"known good existing"
    assert any(
        path.read_bytes() == b"known good existing"
        for path in overwritten.rglob("*")
        if path.is_file()
    )


def test_ambiguous_helper_failure_does_not_restore_after_marker_committed(
    ingest_processor, monkeypatch, tmp_path
):
    source = tmp_path / "incoming.epub"
    source.write_bytes(b"committed replacement")
    existing = tmp_path / "library" / "Author" / "Book" / "Book.epub"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"known good existing")
    _backup_dirs(ingest_processor, monkeypatch, tmp_path)
    processor = _processor(ingest_processor, tmp_path)
    _disable_post_import_work(ingest_processor, processor, monkeypatch)
    processor._incoming_file_is_sane = lambda *_args, **_kwargs: True
    committed = False

    def marker_lookup(_digest):
        return [7] if committed else []

    processor._content_marker_book_ids = marker_lookup

    def transaction(staged, _identity, _imported, _source, _metadata, action):
        nonlocal committed
        if action == "inspect":
            return {"formats": [{"book_id": 7, "path": str(existing)}]}
        shutil.copyfile(staged, existing)
        committed = True
        raise ingest_processor.subprocess.CalledProcessError(1, ["calibre-debug"])

    processor._run_calibre_transaction = transaction
    processor.add_book_to_library(str(source))

    assert processor.last_added_book_id == 7
    assert existing.read_bytes() == b"committed replacement"


def test_folder_label_failure_on_preexisting_marker_retains_source_for_retry(
    ingest_processor, monkeypatch, tmp_path
):
    source = tmp_path / "ingest" / "Owner" / "incoming.epub"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"already imported bytes")
    processor = _processor(ingest_processor, tmp_path)
    processor.filepath = str(source)
    processor.ingest_folder = str(tmp_path / "ingest")
    processor.cwa_settings.update({
        "auto_ingest_folder_label_target": "#removed",
        "auto_ingest_folder_label_nested": False,
    })
    _disable_post_import_work(ingest_processor, processor, monkeypatch)
    processor._content_marker_book_ids = lambda _digest: [7]

    def rejecting_transaction(_staged, _identity, _imported, _source_digest, _metadata, action):
        if action == "inspect":
            return {"status": "already_imported", "book_ids": [7], "formats": []}
        raise ingest_processor.subprocess.CalledProcessError(1, ["calibre-debug"])

    processor._run_calibre_transaction = rejecting_transaction

    with pytest.raises(ingest_processor.RetryIngestSourceError):
        processor.add_book_to_library(str(source))
    assert source.is_file()


def test_existing_folder_label_replay_child_inherits_metadata_exclusion(
    ingest_processor, monkeypatch, tmp_path
):
    """A surviving replay child must retain both maintenance and writer locks."""
    import os
    import json
    from types import SimpleNamespace
    import calibre_library_target

    source = tmp_path / "ingest" / "Owner" / "incoming.epub"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"already imported bytes")
    processor = _processor(ingest_processor, tmp_path)
    processor.filepath = str(source)
    processor.ingest_folder = str(tmp_path / "ingest")
    processor.cwa_settings.update({
        "auto_ingest_folder_label_target": "tags",
        "auto_ingest_folder_label_nested": False,
    })
    _disable_post_import_work(ingest_processor, processor, monkeypatch)
    processor._content_marker_book_ids = lambda _digest: [7]
    monkeypatch.setattr(calibre_library_target, "config_dir", lambda: str(tmp_path))
    original_lock = ingest_processor.metadata_db_write_lock
    writer_inode = []

    @contextmanager
    def tracked_lock():
        with original_lock() as fd:
            writer_inode.append(os.fstat(fd).st_ino)
            yield fd

    monkeypatch.setattr(ingest_processor, "metadata_db_write_lock", tracked_lock)
    inherited = []

    def completed(_command, _environment, **kwargs):
        inherited.extend(os.fstat(fd).st_ino for fd in kwargs.get("pass_fds", ()))
        return SimpleNamespace(stdout="CWNG_INGEST_RESULT=" + json.dumps({
            "status": "already_imported", "book_ids": [7],
        }))

    monkeypatch.setattr(ingest_processor, "_run_calibredb_add_with_retry", completed)
    processor.add_book_to_library(str(source))
    assert writer_inode and writer_inode[0] in inherited, (
        "folder-label child lost the writer gate when its parent exits"
    )
    assert len(inherited) == 2, "raw replay must inherit maintenance and writer descriptors"
    assert processor.last_added_book_id == 7


@pytest.mark.parametrize("fail_replay", [False, True])
def test_concurrent_folder_label_replay_skips_unlocked_format_inspection(
    ingest_processor, monkeypatch, tmp_path, fail_replay
):
    source = tmp_path / "ingest" / "Owner" / "incoming.epub"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"concurrent duplicate")
    processor = _processor(ingest_processor, tmp_path)
    processor.filepath = str(source)
    processor.ingest_folder = str(tmp_path / "ingest")
    processor.cwa_settings.update({
        "auto_ingest_folder_label_target": "tags",
        "auto_ingest_folder_label_nested": False,
    })
    _disable_post_import_work(ingest_processor, processor, monkeypatch)
    lock_held = False

    @contextmanager
    def tracked_lock():
        nonlocal lock_held
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    monkeypatch.setattr(ingest_processor, "metadata_db_write_lock", tracked_lock)
    marker_reads = []

    def marker_lookup(_digest):
        marker_reads.append(lock_held)
        return [7] if lock_held else []

    processor._content_marker_book_ids = marker_lookup
    actions = []

    def transaction(_staged, _identity, _imported, _digest, _metadata, action):
        actions.append(action)
        if fail_replay:
            raise ingest_processor.subprocess.CalledProcessError(1, ["calibre-debug"])
        return {"status": "already_imported", "book_ids": [7]}

    processor._run_calibre_transaction = transaction
    if fail_replay:
        with pytest.raises(ingest_processor.RetryIngestSourceError):
            processor.add_book_to_library(str(source))
        assert source.is_file()
    else:
        processor.add_book_to_library(str(source))

    assert marker_reads == ([False, True, True] if fail_replay else [False, True])
    assert actions == ["apply-folder-labels"]
    assert processor.last_added_book_id == (None if fail_replay else 7)


def test_sanity_check_finishes_before_metadata_write_lock(
    ingest_processor, monkeypatch, tmp_path
):
    source = tmp_path / "incoming.epub"
    source.write_bytes(b"replacement")
    _backup_dirs(ingest_processor, monkeypatch, tmp_path)
    existing = tmp_path / "library" / "Author" / "Book" / "Book.epub"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing")
    processor = _processor(ingest_processor, tmp_path)
    _disable_post_import_work(ingest_processor, processor, monkeypatch)
    lock_held = False

    @contextmanager
    def tracked_lock():
        nonlocal lock_held
        lock_held = True
        try:
            yield
        finally:
            lock_held = False

    monkeypatch.setattr(ingest_processor, "metadata_db_write_lock", tracked_lock)
    processor._incoming_file_is_sane = lambda *_args, **_kwargs: not lock_held

    def transaction(_staged, _identity, _imported, _source, _metadata, action):
        if action == "inspect":
            return {"formats": [{"book_id": 7, "path": str(existing)}]}
        return {"status": "imported", "book_ids": [7]}

    processor._run_calibre_transaction = transaction
    processor.add_book_to_library(str(source))
    assert processor.last_added_book_id == 7


def test_unwritable_failed_quarantine_is_terminal_and_keeps_source(
    ingest_processor, monkeypatch, tmp_path
):
    source = tmp_path / "incoming.epub"
    source.write_bytes(b"broken incoming")
    failed = tmp_path / "processed_books" / "failed"
    failed.mkdir(parents=True)
    monkeypatch.setattr(ingest_processor, "backup_destinations", {"failed": str(failed)})
    processor = _processor(ingest_processor, tmp_path)
    _disable_post_import_work(ingest_processor, processor, monkeypatch)
    processor._incoming_file_is_sane = lambda *_args, **_kwargs: False
    processor._run_calibre_transaction = lambda *_args: {
        "formats": [{"book_id": 7, "path": str(tmp_path / "existing.epub")}]
    }

    original_copy = shutil.copy

    def unwritable_failed_copy(input_file, output_path):
        if Path(output_path) == failed:
            raise PermissionError("processed_books/failed is unwritable")
        return original_copy(input_file, output_path)

    monkeypatch.setattr(ingest_processor.shutil, "copy", unwritable_failed_copy)

    with pytest.raises(ingest_processor.PreserveIngestSourceError):
        processor.add_book_to_library(str(source))
    assert source.read_bytes() == b"broken incoming"


def test_main_returns_terminal_exit_without_deleting_after_quarantine_failure(
    ingest_processor, monkeypatch, tmp_path
):
    source = tmp_path / "incoming.epub"
    source.write_bytes(b"broken incoming")
    delete_called = False

    class FailingProcessor:
        filename = source.name
        ingest_ignored_formats = []
        cwa_settings = {"ingest_timeout_minutes": 15}
        input_format = "epub"
        is_target_format = True

        @staticmethod
        def is_file_in_use():
            return True

        @staticmethod
        def add_book_to_library(_path):
            raise ingest_processor.PreserveIngestSourceError("quarantine unavailable")

        @staticmethod
        def set_library_permissions():
            pass

        @staticmethod
        def delete_current_file():
            nonlocal delete_called
            delete_called = True

    monkeypatch.setattr(ingest_processor, "_acquire_process_lock_or_exit", lambda: None)
    monkeypatch.setattr(ingest_processor, "initialize_runtime", lambda: True)
    monkeypatch.setattr(ingest_processor, "NewBookProcessor", lambda _path: FailingProcessor())
    monkeypatch.setattr(ingest_processor, "is_a_book_format", lambda _format: True)

    assert ingest_processor.main(str(source)) == 3
    assert source.read_bytes() == b"broken incoming"
    assert not delete_called


def test_recovery_copy_uses_digest_and_retention_limit(
    ingest_processor, monkeypatch, tmp_path
):
    recovery = tmp_path / "overwritten"
    recovery.mkdir()
    for index in range(3):
        old = recovery / f"old-{index}"
        old.mkdir()
        (old / "book.epub").write_bytes(str(index).encode())
    existing = tmp_path / "good.epub"
    existing.write_bytes(b"known good")
    monkeypatch.setenv("CWA_INGEST_OVERWRITE_RECOVERY_MAX_SETS", "2")
    monkeypatch.setattr(ingest_processor, "backup_destinations", {"overwritten": str(recovery)})
    processor = _processor(ingest_processor, tmp_path)

    assert processor._preserve_overwritten_formats([existing], tmp_path / "incoming.epub")
    recovery_sets = [item for item in recovery.iterdir() if item.is_dir()]
    assert len(recovery_sets) == 2
    assert any(
        file.read_bytes() == b"known good"
        for directory in recovery_sets for file in directory.iterdir()
    )


def test_raw_import_owns_maintenance_but_remote_post_processing_does_not(
        ingest_processor, monkeypatch, tmp_path):
    import calibre_library_target as routing
    monkeypatch.setattr(routing, "config_dir", lambda: str(tmp_path))
    source = tmp_path / "new.epub"
    source.write_bytes(b"new book")
    p = _processor(ingest_processor, tmp_path)
    p.cwa_settings["auto_ingest_automerge"] = "new_record"
    _disable_post_import_work(ingest_processor, p, monkeypatch)
    observed = []
    def transaction(*_args):
        assert routing.ownership.busy(str(tmp_path), "maintenance")
        return {"status": "imported", "book_ids": [7]}
    p._run_calibre_transaction = transaction
    p.fetch_metadata_if_enabled = lambda **_kw: observed.append(
        routing.ownership.busy(str(tmp_path), "maintenance"))
    p.add_book_to_library(str(source))
    assert observed == [False], "network metadata fetch unnecessarily holds the offline library"
    assert not routing.ownership.busy(str(tmp_path), "maintenance")


def test_committed_import_finishes_followups_when_conversion_takes_maintenance(
        ingest_processor, monkeypatch, tmp_path):
    import sqlite3
    import calibre_library_target as routing
    monkeypatch.setattr(routing, "config_dir", lambda: str(tmp_path))
    app_db = tmp_path / "app.db"
    with sqlite3.connect(app_db) as con:
        con.execute("CREATE TABLE settings (config_unicode_filename INTEGER)")
        con.execute("INSERT INTO settings VALUES (0)")
    monkeypatch.setattr(ingest_processor, "get_app_db_path", lambda: str(app_db))
    source = tmp_path / "new.epub"
    source.write_bytes(b"new book committed before competing conversion")
    p = _processor(ingest_processor, tmp_path)
    p.cwa_settings["auto_ingest_automerge"] = "new_record"
    _disable_post_import_work(ingest_processor, p, monkeypatch)
    p._fix_unicode_path = ingest_processor.NewBookProcessor._fix_unicode_path.__get__(p)
    p._run_calibre_transaction = lambda *_a: {"status": "imported", "book_ids": [7]}
    maintenance = routing.ownership.maintenance(str(tmp_path))
    followups = []
    p.fetch_metadata_if_enabled = lambda **_kw: maintenance.__enter__()
    p.generate_missing_cover_if_enabled = lambda _id: followups.append("cover")
    p.trigger_auto_send_if_enabled = lambda **_kw: followups.append("send")
    try:
        p.add_book_to_library(str(source))
    finally:
        maintenance.__exit__(None, None, None)
    assert followups == ["cover", "send"], "a busy optional path fix skipped the committed book's followups"
    assert p.last_added_book_id == 7


@pytest.mark.parametrize("busy", [False, True])
def test_generated_cover_coordinates_raw_metadata_and_skips_busy_optional_work(
    ingest_processor, monkeypatch, tmp_path, busy
):
    import sqlite3
    from types import SimpleNamespace
    from cps.services import cover_generator

    processor = _processor(ingest_processor, tmp_path)
    book_dir = Path(processor.library_dir) / "Author/Book (1)"
    book_dir.mkdir(parents=True)
    with sqlite3.connect(processor.metadata_db) as connection:
        connection.executescript(
            "CREATE TABLE books(id INTEGER, path TEXT, title TEXT, has_cover INTEGER, series_index REAL);"
            "INSERT INTO books VALUES(1, 'Author/Book (1)', 'Book', 0, 1);"
            "CREATE TABLE authors(id INTEGER, name TEXT);"
            "CREATE TABLE books_authors_link(id INTEGER, book INTEGER, author INTEGER);"
            "CREATE TABLE series(id INTEGER, name TEXT);"
            "CREATE TABLE books_series_link(book INTEGER, series INTEGER);"
        )
    active = []

    @contextmanager
    def offline():
        if busy:
            raise TimeoutError("library maintenance busy")
        active.append("maintenance")
        try:
            yield
        finally:
            active.pop()

    @contextmanager
    def metadata():
        assert active == ["maintenance"], "raw cover access acquired no maintenance ownership"
        active.append("metadata")
        try:
            yield
        finally:
            active.pop()

    ownership_at_write = []

    def generate(destination, *_args, **_kwargs):
        ownership_at_write.append(list(active))
        Path(destination).write_bytes(b"generated cover")
        return True

    monkeypatch.setattr(ingest_processor, "offline_library_access", offline)
    monkeypatch.setattr(ingest_processor, "metadata_db_write_lock", metadata)
    monkeypatch.setattr(cover_generator, "settings_from_app_db", lambda _path: SimpleNamespace(auto_enabled=True, default_preset="classic"))
    monkeypatch.setattr(cover_generator, "generate_cover_file", generate)
    assert processor.generate_missing_cover_if_enabled(1) is (not busy)
    with sqlite3.connect(processor.metadata_db) as connection:
        assert connection.execute("SELECT has_cover FROM books WHERE id=1").fetchone()[0] == (0 if busy else 1)
    assert (book_dir / "cover.jpg").exists() is (not busy)
    assert ownership_at_write == ([] if busy else [["maintenance", "metadata"]])
    assert active == []
