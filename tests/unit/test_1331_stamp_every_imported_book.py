# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for the second half of fork #1331 — a freshly imported book
sorting into the *middle* of "Newest" rather than the top.

#1331 was first fixed as a tie-break problem: every book list paged with
LIMIT/OFFSET over an ORDER BY naming one non-unique column, so tied rows came
back in whatever order SQLite's plan walked. That fix is real and stays.

It is not the whole story, and @Oakwhisper said so on the thread: *"It wasn't
tie-breaking because there was no tie. In my case I had only added one book
that day and it was showing up in the middle of the list instead of at the
beginning."* A total order cannot put a book at the top if its stored
``timestamp`` genuinely is not recent — and for imported books it often is not.

``calibredb add`` derives ``books.timestamp`` from the file's own metadata,
which for most EPUBs is the **publication date**. ``ingest_processor`` knows
this and corrects it back to the import time, but it corrected only
``last_added_book_id`` — a single id — while ``_parse_added_book_ids`` exists
precisely because one add can report several (``Added book ids: 4, 5``). Every
book in a multi-id add except the last therefore kept a publication date as its
"date added", and sorted wherever that date fell: 1998 for an old novel, i.e.
the middle of the library.

That is the reported symptom exactly, it needs no tie to happen, and it
survived the tie-break fix. These tests pin the correction covering the whole
batch. ``test_prefix_shape_leaves_batch_stranded`` is the control: it applies
the old single-id logic to the same fixture and asserts the symptom returns, so
these tests fail if the call site regresses to stamping one id.
"""

from __future__ import annotations

import sqlite3

import pytest


# The library the user already had: ordinary books, real import dates.
EXISTING = [
    (101, "Existing A", "2026-08-01 10:00:00+00:00"),
    (102, "Existing B", "2026-08-02 10:00:00+00:00"),
    (103, "Existing C", "2026-08-03 10:00:00+00:00"),
]

# One `calibredb add` reporting three ids. calibredb stamped each with the
# book's publication date, so left uncorrected they scatter through the library.
IMPORTED_WITH_PUBDATES = [
    (201, "Imported 1998", "1998-04-11 00:00:00+00:00"),
    (202, "Imported 2005", "2005-09-02 00:00:00+00:00"),
    (203, "Imported 2012", "2012-01-30 00:00:00+00:00"),
]

IMPORT_TIME = "2026-08-14 21:00:00+00:00"


@pytest.fixture
def library():
    """An in-memory metadata.db-shaped ``books`` table, mid-import."""
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE books ("
        " id INTEGER PRIMARY KEY,"
        " title TEXT,"
        " timestamp TIMESTAMP,"
        " last_modified TIMESTAMP)"
    )
    for book_id, title, ts in EXISTING + IMPORTED_WITH_PUBDATES:
        con.execute(
            "INSERT INTO books (id, title, timestamp, last_modified) VALUES (?,?,?,?)",
            (book_id, title, ts, ts),
        )
    con.commit()
    yield con
    con.close()


def newest_first(con):
    """The ids "Newest" shows, in order, using the #1331 total order."""
    return [
        row[0]
        for row in con.execute(
            "SELECT id FROM books ORDER BY timestamp DESC, id DESC"
        )
    ]


def test_premise_uncorrected_import_lands_in_the_middle(library):
    """Guard the fixture's premise: this is the bug, before any correction.

    Without it a later refactor could make the other tests pass vacuously.
    """
    order = newest_first(library)
    assert order[:3] == [103, 102, 101], "existing books should lead while imports are unstamped"
    for imported_id in (201, 202, 203):
        assert order.index(imported_id) >= 3, "an unstamped import should not be at the top"


def test_every_imported_book_is_stamped_and_leads_the_list(library):
    """The whole batch gets the import time, so all three lead "Newest"."""
    import ingest_processor

    affected = ingest_processor.stamp_books_with_import_time(
        library, [201, 202, 203], now=IMPORT_TIME
    )
    assert affected == 3

    order = newest_first(library)
    assert order[:3] == [203, 202, 201], (
        "every book from one add should sit at the top of Newest, id-desc among themselves"
    )
    assert order[3:] == [103, 102, 101]


def test_prefix_shape_leaves_batch_stranded(library):
    """Control: the old single-id correction reproduces @Oakwhisper's symptom.

    This is what the code did before the fix. It is asserted rather than
    described so that a regression to stamping one id fails a test instead of
    quietly restoring the bug.
    """
    library.execute(
        "UPDATE books SET timestamp = ? WHERE id = ?", (IMPORT_TIME, 203)
    )

    order = newest_first(library)
    assert order[0] == 203, "the last id of the add is fine — it is the one that got stamped"
    # The other two are still carrying publication dates, stranded mid-library.
    assert order.index(201) > order.index(101), "1998 import sorts below a real 2026 book"
    assert order.index(202) > order.index(101), "2005 import sorts below a real 2026 book"


def test_single_book_add_still_works(library):
    """The common case — one file, one id — keeps behaving."""
    import ingest_processor

    assert ingest_processor.stamp_books_with_import_time(
        library, [201], now=IMPORT_TIME
    ) == 1
    assert newest_first(library)[0] == 201


def test_ignores_none_duplicates_and_junk_ids(library):
    """Callers pass whatever calibredb parsing produced; never raise on it."""
    import ingest_processor

    affected = ingest_processor.stamp_books_with_import_time(
        library, [201, 201, None, "202", "not-an-id"], now=IMPORT_TIME
    )
    assert affected == 2, "201 counted once, '202' coerced, junk and None dropped"

    assert ingest_processor.stamp_books_with_import_time(library, [], now=IMPORT_TIME) == 0
    assert ingest_processor.stamp_books_with_import_time(library, None, now=IMPORT_TIME) == 0


def test_call_site_passes_the_whole_batch():
    """Source pin: the ingest path must hand over every id, not the last one.

    The behavioural tests above exercise the helper directly, so they stay green
    even if the call site regresses to ``last_added_book_id``. This pins the
    wiring that actually reaches a user.
    """
    import inspect

    import ingest_processor

    source = inspect.getsource(ingest_processor)

    assert "stamp_books_with_import_time(con, imported_ids, now)" in source, (
        "the ingest path should stamp the collected batch"
    )
    assert "self.last_added_book_ids or (" in source, (
        "imported_ids should prefer the plural list, falling back to the single id"
    )
    assert "UPDATE books SET timestamp = ? WHERE id = ?" not in source, (
        "the single-id import stamp is the bug; it should not come back"
    )
