# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""#2371: post-import checksums on a split library must use the real metadata.db.

On a split library NewBookProcessor.__init__ computes self.metadata_db from the
database directory and then repoints self.library_dir at book storage.
generate_book_checksums() rebuilt the database path from library_dir, so every
import failed with "no such table: books" and sqlite3.connect() left an empty
metadata.db behind in the book-storage folder.

Reported and first fixed by @sgreadly (#2371, #2372).
"""

import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

BOOK_ID = 553
BOOK_PATH = "Stephen Fry/Moab Is My Washpot (553)"
BASENAME = "Moab Is My Washpot - Stephen Fry"


@pytest.fixture()
def processor_cls(monkeypatch):
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    from ingest_processor import NewBookProcessor
    return NewBookProcessor


def _metadata_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, path TEXT, timestamp TEXT);
        CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER, format TEXT, name TEXT);
        CREATE TABLE book_format_checksums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book INTEGER NOT NULL,
            format TEXT NOT NULL COLLATE NOCASE,
            checksum TEXT NOT NULL,
            version TEXT NOT NULL,
            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    con.execute("INSERT INTO books VALUES (?, 'Moab Is My Washpot', ?, '2026-09-29')", (BOOK_ID, BOOK_PATH))
    con.execute("INSERT INTO data VALUES (1, ?, 'EPUB', ?)", (BOOK_ID, BASENAME))
    con.commit()
    con.close()


def _split_library(processor_cls, tmp_path):
    """The state __init__ leaves on a split library: metadata_db in the
    database directory, library_dir repointed at book storage."""
    db_dir = tmp_path / "calibre-library"
    storage = tmp_path / "mnt" / "calibre"
    db_dir.mkdir(parents=True)
    _metadata_db(db_dir / "metadata.db")
    book_dir = storage / BOOK_PATH
    book_dir.mkdir(parents=True)
    (book_dir / f"{BASENAME}.epub").write_bytes(b"split library ingest" * 1000)

    processor = object.__new__(processor_cls)
    processor.metadata_db = str(db_dir / "metadata.db")
    processor.library_dir = str(storage)
    return processor, db_dir, storage


def _checksum_rows(db_path: Path) -> dict:
    con = sqlite3.connect(db_path)
    try:
        return dict(con.execute(
            "SELECT version, checksum FROM book_format_checksums WHERE book = ?", (BOOK_ID,)
        ).fetchall())
    finally:
        con.close()


def test_split_library_import_stores_checksums_in_the_real_database(processor_cls, tmp_path):
    processor, db_dir, storage = _split_library(processor_cls, tmp_path)

    processor.generate_book_checksums("Moab Is My Washpot", book_id=BOOK_ID)

    rows = _checksum_rows(db_dir / "metadata.db")
    assert set(rows) == {"koreader", "koreader_filename"}
    assert rows["koreader_filename"] == hashlib.md5(f"{BASENAME}.epub".encode()).hexdigest()
    assert not (storage / "metadata.db").exists(), "a stray metadata.db was created in book storage"


def test_split_library_missing_book_file_stores_nothing_and_creates_nothing(processor_cls, tmp_path):
    processor, db_dir, storage = _split_library(processor_cls, tmp_path)
    shutil.rmtree(storage / "Stephen Fry")

    processor.generate_book_checksums("Moab Is My Washpot", book_id=BOOK_ID)

    assert _checksum_rows(db_dir / "metadata.db") == {}
    assert not (storage / "metadata.db").exists()
