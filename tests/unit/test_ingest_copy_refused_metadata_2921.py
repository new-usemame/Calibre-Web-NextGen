# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Ingest must not fail a book on metadata the destination refuses (#2921).

The #2921 reporter, with ``/config`` and the ingest folder on NFSv4, also saw
``[Errno 524]`` while ingesting: when the EPUB fixer backed the original up to
``processed_books/fixed_originals`` and when the book was staged for import.
Those copies used ``shutil.copy2``; its ``copystat`` step writes back the
``system.nfs4_acl`` extended attribute, which NFSv4 refuses with 524 after the
data has already been copied. ``copy2`` then raises, and the ingest treated a
complete copy as a failure: the staged book went to ``failed`` instead of the
library.

Each test refuses ``copystat`` the way NFSv4 does and drives the real code
path, asserting that the book is staged (or backed up) instead of failed.
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(REPO_ROOT / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import ingest_processor  # noqa: E402

ENOTSUPP = 524  # Linux kernel-internal "operation not supported", leaked by NFSv4


@pytest.fixture
def nfs_refuses_metadata(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise OSError(ENOTSUPP, "Unknown error 524")
    monkeypatch.setattr(shutil, "copystat", refuse)


def _processor(tmp_path, **extra):
    stub = object.__new__(ingest_processor.NewBookProcessor)
    staging = tmp_path / "staging"
    staging.mkdir()
    backups = []
    stub.__dict__.update(dict(
        target_format="epub",
        is_kindle_epub_fixer=False,
        tmp_conversion_dir=str(tmp_path / "tmp") + os.sep,
        staging_dir=str(staging),
        cwa_settings={},
        metadata_db="/nonexistent/metadata.db",
        library_dir=str(tmp_path / "library"),
        filepath=str(tmp_path / "ingest" / "book.epub"),
        backup=lambda path, backup_type=None: backups.append(backup_type),
        **extra,
    ))
    return stub, staging, backups


def _book(tmp_path):
    ingest = tmp_path / "ingest"
    ingest.mkdir()
    book = ingest / "book.epub"
    book.write_bytes(b"PK\x03\x04 epub bytes")
    return book


def _capture_import_start(monkeypatch, staging):
    """Stop at the first step after staging and record what calibredb would get."""
    handed_to_calibredb = []

    def import_starts():
        handed_to_calibredb.extend(p.read_bytes() for p in staging.iterdir())
        raise RuntimeError("no calibredb under test")

    monkeypatch.setattr(ingest_processor, "mark_ingest_batch_active", import_starts)
    return handed_to_calibredb


def test_new_book_is_staged_when_the_staging_copy_refuses_metadata(
        tmp_path, nfs_refuses_metadata, monkeypatch, capsys):
    book = _book(tmp_path)
    stub, staging, backups = _processor(tmp_path)
    handed_to_calibredb = _capture_import_start(monkeypatch, staging)

    try:
        ingest_processor.NewBookProcessor.add_book_to_library(stub, str(book))
    except Exception:
        pass

    out = capsys.readouterr().out
    assert "Failed to stage file for import" not in out, out
    assert handed_to_calibredb == [book.read_bytes()], (
        "a complete copy whose metadata NFSv4 refused must still be imported, "
        "not moved to the failed folder")


def test_new_format_is_staged_when_the_staging_copy_refuses_metadata(
        tmp_path, nfs_refuses_metadata, monkeypatch, capsys):
    book = _book(tmp_path)
    stub, staging, backups = _processor(
        tmp_path, _validate_book_exists=lambda _book_id: True)
    handed_to_calibredb = _capture_import_start(monkeypatch, staging)

    try:
        ingest_processor.NewBookProcessor.add_format_to_book(stub, 7, str(book))
    except Exception:
        pass

    out = capsys.readouterr().out
    assert "Failed to stage file for add_format" not in out, out
    assert handed_to_calibredb == [book.read_bytes()], (
        "calibredb add_format must receive the complete staged copy")


def test_epub_fixer_backs_up_the_original_when_metadata_is_refused(
        tmp_path, nfs_refuses_metadata, monkeypatch, capsys):
    import app_paths
    import kindle_epub_fixer

    processed = tmp_path / "processed_books"
    monkeypatch.setattr(app_paths, "processed_books_dir", lambda: processed)
    book = _book(tmp_path)
    fixer = object.__new__(kindle_epub_fixer.EPUBFixer)
    fixer.cwa_settings = {"auto_backup_epub_fixes": True}
    fixer.manually_triggered = False

    fixer.backup_original_file(str(book))

    out = capsys.readouterr().out
    assert "Error occurred when backing up" not in out, out
    assert (processed / "fixed_originals" / "book.epub").read_bytes() == book.read_bytes()


def test_epub_fixer_backup_does_not_start_the_web_app(tmp_path):
    """The fixer runs as its own process. Loading the copy helper through the
    cps package would initialise the app there, including the web server's
    SIGTERM handler, so a cancelled fixer would keep running."""
    import subprocess
    src = tmp_path / "book.epub"
    src.write_bytes(b"PK epub")
    code = (
        "import signal, sys; sys.path.insert(0, sys.argv[1]);"
        "import library_copy; library_copy.copy_file(sys.argv[2], sys.argv[3]);"
        "assert 'cps' not in sys.modules, 'cps package was imported';"
        "assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL, 'SIGTERM handler replaced';"
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, SCRIPTS_DIR, str(src), str(tmp_path / "copy.epub")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.stdout.strip() == "ok", result.stderr
    assert (tmp_path / "copy.epub").read_bytes() == b"PK epub"
