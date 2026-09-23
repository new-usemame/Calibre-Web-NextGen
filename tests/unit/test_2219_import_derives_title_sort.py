# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for fork #2219 — imported books keeping a title sort that
is just their title, so "The Donkey" files under T.

``calibredb add`` (calibre 9.0 and the image's 9.11, measured against a fresh
library) stores a file's embedded ``calibre:title_sort`` verbatim and applies
the title-sort rule only to a file without one:

    The Donkey     (embedded title_sort "The Donkey")  ->  sort "The Donkey"
    The Barn Door  (no embedded title_sort)            ->  sort "Barn Door, The"

which is @bcsteeve's report exactly. calibre's ``books_update_trg`` re-derives
``sort`` only when the title changes, so the book stayed under T through every
later metadata save. The fixture below is calibre's own ``books`` table and
triggers, and ``title_sort`` is the ingest processor's real registration, so the
tests exercise the same SQL the import runs against.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest


DEFAULT_TITLE_REGEX = (
    r"^(A|The|An|Der|Die|Das|Den|Ein|Eine|Einen|Dem|Des|Einem|Eines|Le|La|Les|L\'|Un|Une)\s+"
)

# calibre's books table and the two triggers that write ``sort``, copied from a
# metadata.db created by ``calibredb add --with-library`` on an empty folder.
CALIBRE_BOOKS_DDL = """
CREATE TABLE books ( id      INTEGER PRIMARY KEY AUTOINCREMENT,
                     title     TEXT NOT NULL DEFAULT 'Unknown' COLLATE NOCASE,
                     sort      TEXT COLLATE NOCASE,
                     timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                     uuid TEXT,
                     last_modified TIMESTAMP NOT NULL DEFAULT '2000-01-01 00:00:00+00:00');
CREATE TRIGGER books_insert_trg AFTER INSERT ON books
    BEGIN
        UPDATE books SET sort=title_sort(NEW.title) WHERE id=NEW.id;
    END;
CREATE TRIGGER books_update_trg AFTER UPDATE ON books
    BEGIN
        UPDATE books SET sort=title_sort(NEW.title)
                     WHERE id=NEW.id AND OLD.title <> NEW.title;
    END;
"""


def calibredb_add(con, book_id, title, embedded_title_sort=None):
    """Insert a book the way calibredb add leaves it: rule-derived sort, then
    overwritten by the file's embedded title sort when it has one."""
    con.execute("INSERT INTO books (id, title) VALUES (?, ?)", (book_id, title))
    if embedded_title_sort is not None:
        con.execute("UPDATE books SET sort = ? WHERE id = ?", (embedded_title_sort, book_id))


def sort_of(con, book_id):
    return con.execute("SELECT sort FROM books WHERE id = ?", (book_id,)).fetchone()[0]


@pytest.fixture
def library():
    import ingest_processor

    con = sqlite3.connect(":memory:")
    registered = ingest_processor.NewBookProcessor._register_title_sort_function(
        SimpleNamespace(_title_sort_regex=DEFAULT_TITLE_REGEX), con
    )
    assert registered, "the ingest processor's title_sort must register"
    con.executescript(CALIBRE_BOOKS_DDL)
    # Already in the library before this import; must never be touched.
    calibredb_add(con, 1, "The Existing Book", embedded_title_sort="The Existing Book")
    # This import's batch.
    calibredb_add(con, 10, "The Donkey", embedded_title_sort="The Donkey")
    calibredb_add(con, 11, "The Barn Door")
    calibredb_add(con, 12, "The Hobbit", embedded_title_sort="Tolkien 01")
    calibredb_add(con, 13, "Moby Dick", embedded_title_sort="Moby Dick")
    calibredb_add(con, 14, "Der Prozess", embedded_title_sort="Der Prozess")
    con.commit()
    yield con
    con.close()


def test_premise_the_embedded_bare_title_survives_calibredb_add(library):
    """Guard the fixture: this is the reported state before any correction."""
    assert sort_of(library, 10) == "The Donkey"
    assert sort_of(library, 11) == "Barn Door, The"


def test_import_files_the_bare_title_sort_under_its_rule(library):
    """The Donkey sorts under D next to The Barn Door under B; deliberate sorts,
    titles the rule leaves alone and books outside the batch keep theirs."""
    import ingest_processor

    resorted = ingest_processor.derive_title_sort_for_unsorted_imports(
        library, [10, 11, 12, 13, 14]
    )

    assert sort_of(library, 10) == "Donkey, The"
    assert sort_of(library, 14) == "Prozess, Der", "the configured regex, not English-only"
    assert sort_of(library, 11) == "Barn Door, The"
    assert sort_of(library, 12) == "Tolkien 01", "a sort that differs from the title is a choice"
    assert sort_of(library, 13) == "Moby Dick"
    assert sort_of(library, 1) == "The Existing Book", "only this import's books are touched"
    assert resorted == 2

    order = [row[0] for row in library.execute(
        "SELECT title FROM books WHERE id >= 10 ORDER BY sort")]
    assert order == ["The Barn Door", "The Donkey", "Moby Dick", "Der Prozess", "The Hobbit"]


def test_ignores_none_duplicates_and_junk_ids(library):
    """Callers pass whatever calibredb parsing produced; never raise on it."""
    import ingest_processor

    assert ingest_processor.derive_title_sort_for_unsorted_imports(
        library, [10, 10, None, "14", "not-an-id"]) == 2
    assert ingest_processor.derive_title_sort_for_unsorted_imports(library, []) == 0
    assert ingest_processor.derive_title_sort_for_unsorted_imports(library, None) == 0
