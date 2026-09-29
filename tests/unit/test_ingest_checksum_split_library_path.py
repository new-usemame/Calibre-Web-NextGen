# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression test: NewBookProcessor.generate_book_checksums() must open the
real metadata.db, not one reconstructed from self.library_dir.

For a split library, __init__ computes self.metadata_db first and only then
repoints self.library_dir at the book-storage path (self.split_library
["split_path"]) — see lines around 1214-1219. generate_book_checksums()
rebuilt the database path from self.library_dir instead of using the
already-correct self.metadata_db, so on a split library it opened
"<book_storage_path>/metadata.db" instead. That path doesn't exist, and
sqlite3.connect() creates an empty file the instant it connects — before any
query runs — so every checksum attempt on a split library both failed with
"no such table: books" *and* left a stray, growing-forever empty metadata.db
sitting in the book-storage directory (reported: on an NFS share) on every
single ingest.

On a plain (non-split) library self.library_dir and self.metadata_db's
directory are the same path, which is why this was never caught there.
"""

import shutil
import sqlite3
import sys
import types
from pathlib import Path

import pytest


def _install_stub_checksums_module(monkeypatch):
    """generate_book_checksums() does a deferred `from
    cps.progress_syncing.checksums import ...` (see the comment at that call
    site: the full `cps` package import triggers Flask app singleton init,
    which can hang under pytest-xdist and drags in the whole dependency
    tree). The real calculate_and_store_checksum() is exercised by
    tests/unit/test_generate_checksums.py already; these tests are only
    about *which database path* generate_book_checksums() opens, so a
    same-shape stub is used instead of the real cps package.
    """
    checksums_pkg = types.ModuleType("cps.progress_syncing.checksums")

    def calculate_and_store_checksum(book_id, book_format, file_path, db_connection=None,
                                      filename_for_matching=None):
        import os as _os
        if not _os.path.exists(file_path):
            return None
        checksum = "stub-checksum-" + book_format.lower()
        db_connection.execute(
            "INSERT INTO book_format_checksums (book, format, checksum, version, created) "
            "VALUES (?, ?, ?, ?, ?)",
            (book_id, book_format.upper(), checksum, "1", "2026-09-29T00:00:00+00:00"),
        )
        return checksum

    checksums_pkg.calculate_and_store_checksum = calculate_and_store_checksum
    checksums_pkg.CHECKSUM_VERSION = "1"

    progress_syncing_pkg = types.ModuleType("cps.progress_syncing")
    progress_syncing_pkg.checksums = checksums_pkg
    cps_pkg = sys.modules.get("cps") or types.ModuleType("cps")
    cps_pkg.progress_syncing = progress_syncing_pkg

    monkeypatch.setitem(sys.modules, "cps", cps_pkg)
    monkeypatch.setitem(sys.modules, "cps.progress_syncing", progress_syncing_pkg)
    monkeypatch.setitem(sys.modules, "cps.progress_syncing.checksums", checksums_pkg)

pytestmark = pytest.mark.unit


def _real_metadata_db(path: Path) -> None:
    """A metadata.db with just enough schema for the method under test."""
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE books (id INTEGER PRIMARY KEY, path TEXT, title TEXT, timestamp TEXT);
        CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER, format TEXT, name TEXT);
        CREATE TABLE book_format_checksums (
            id INTEGER PRIMARY KEY,
            book INTEGER,
            format TEXT,
            checksum TEXT,
            version TEXT,
            created TEXT
        );
        INSERT INTO books (id, path, title, timestamp) VALUES (553, 'Stephen Fry/Moab Is My Washpot (553)', 'Moab Is My Washpot', '2026-09-29');
        INSERT INTO data (book, format, name) VALUES (553, 'EPUB', 'Moab Is My Washpot');
        """
    )
    con.commit()
    con.close()


def build_split_library_processor(ingest_processor, tmp_path, *, book_bytes=b"epub bytes"):
    """A processor in the post-__init__ state of a split-library instance:
    self.metadata_db already correct, self.library_dir already repointed at
    book storage — exactly what __init__ leaves behind (lines 1214-1219)."""
    calibre_library = tmp_path / "calibre-library"  # /calibre-library in prod
    book_storage = tmp_path / "mnt" / "calibre"  # /mnt/calibre in prod, NFS-backed
    calibre_library.mkdir(parents=True)
    book_storage.mkdir(parents=True)

    _real_metadata_db(calibre_library / "metadata.db")

    book_dir = book_storage / "Stephen Fry" / "Moab Is My Washpot (553)"
    book_dir.mkdir(parents=True)
    (book_dir / "Moab Is My Washpot.epub").write_bytes(book_bytes)

    processor = object.__new__(ingest_processor.NewBookProcessor)
    processor.metadata_db = str(calibre_library / "metadata.db")
    processor.library_dir = str(book_storage)  # reassigned by split-library __init__
    processor.split_library = {"split_path": str(book_storage)}
    return processor, calibre_library, book_storage


@pytest.fixture()
def ingest_processor(monkeypatch):
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    _install_stub_checksums_module(monkeypatch)
    import ingest_processor as mod
    return mod


def test_checksum_generation_finds_the_split_library_database(ingest_processor, tmp_path):
    processor, calibre_library, book_storage = build_split_library_processor(ingest_processor, tmp_path)

    processor.generate_book_checksums("Moab Is My Washpot", book_id=553)

    con = sqlite3.connect(calibre_library / "metadata.db")
    row = con.execute(
        "SELECT checksum FROM book_format_checksums WHERE book = 553 AND format = 'EPUB'"
    ).fetchone()
    assert row is not None and row[0], (
        "checksum was not stored in the real metadata.db — "
        "generate_book_checksums() looked somewhere else"
    )


def test_checksum_generation_does_not_touch_book_storage_directory(ingest_processor, tmp_path):
    """The stray-file side effect from the report: opening the wrong path
    creates a 0-byte metadata.db in the book-storage directory the moment
    sqlite3 connects, before the "no such table" error is even raised."""
    processor, calibre_library, book_storage = build_split_library_processor(ingest_processor, tmp_path)

    processor.generate_book_checksums("Moab Is My Washpot", book_id=553)

    assert not (book_storage / "metadata.db").exists(), (
        "a metadata.db was created in the book-storage directory — "
        "this is the stray-file side effect reported against /mnt/calibre"
    )


def test_checksum_generation_still_works_on_a_plain_library(ingest_processor, tmp_path):
    """Guard: a plain (non-split) library, where library_dir and metadata_db's
    directory were always the same path, must keep working."""
    library = tmp_path / "calibre-library"
    library.mkdir()
    _real_metadata_db(library / "metadata.db")
    book_dir = library / "Stephen Fry" / "Moab Is My Washpot (553)"
    book_dir.mkdir(parents=True)
    (book_dir / "Moab Is My Washpot.epub").write_bytes(b"epub bytes")

    processor = object.__new__(ingest_processor.NewBookProcessor)
    processor.metadata_db = str(library / "metadata.db")
    processor.library_dir = str(library)
    processor.split_library = None

    processor.generate_book_checksums("Moab Is My Washpot", book_id=553)

    con = sqlite3.connect(library / "metadata.db")
    row = con.execute(
        "SELECT checksum FROM book_format_checksums WHERE book = 553 AND format = 'EPUB'"
    ).fetchone()
    assert row is not None and row[0]


def test_missing_book_file_is_reported_without_crashing(ingest_processor, tmp_path):
    """Guard: this method already handles a missing format file gracefully
    (prints a WARN and continues) — the fix must not disturb that path."""
    processor, calibre_library, _ = build_split_library_processor(ingest_processor, tmp_path)
    # Remove the book file the row in metadata.db points at.
    shutil.rmtree(Path(processor.split_library["split_path"]) / "Stephen Fry")

    processor.generate_book_checksums("Moab Is My Washpot", book_id=553)  # must not raise

    con = sqlite3.connect(calibre_library / "metadata.db")
    row = con.execute(
        "SELECT checksum FROM book_format_checksums WHERE book = 553 AND format = 'EPUB'"
    ).fetchone()
    assert row is None
