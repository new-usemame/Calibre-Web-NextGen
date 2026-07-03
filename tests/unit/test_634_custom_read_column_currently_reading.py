# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for fork #634 — with a Calibre custom read column
configured, the "Currently Reading" magic shelf showed every book marked
read and never the book actually in progress.

Root cause: build_filter_from_rule's custom-column branch coerced the rule
value with bool(), so the currently_reading preset's value 2
(STATUS_IN_PROGRESS) collapsed into bool(2) == True — the exact same filter
as "Read". The in-progress tri-state lives only in ub.ReadBook (KOReader/
Kobo sync writes it there regardless of config_read_column, and the #312
mirror only writes FINISHED to the column), so the custom-column path must
overlay it from ReadBook instead of pretending the boolean column carries it.

Same class of bug pinned for the detail page: show_book's read_status_raw
was the raw column value in custom-column mode, so the "currently reading"
badge (detail.html, fork #509) could never render for those users.

Behavioral tests run the REAL filter against in-memory SQLite: a real bool
cc class built by CalibreDB.setup_db_cc_classes, real Books rows, and a real
ub.ReadBook session — mocks would pin the call shape, not the SQL semantics.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.unit

# High id so we can't collide with cc classes another test may have created.
CC_ID = 934

USER_A = 1
USER_B = 2

BOOK_READING = 101      # ReadBook IN_PROGRESS for USER_A, no cc row
BOOK_FINISHED = 102     # cc row True (marked read), ReadBook FINISHED
BOOK_UNTOUCHED = 103    # no cc row, no ReadBook row
BOOK_READ_STALE = 104   # cc row True AND stale ReadBook IN_PROGRESS
BOOK_OTHER_USER = 105   # ReadBook IN_PROGRESS for USER_B only


@pytest.fixture(scope="module")
def cc_class():
    """Create the bool cc class ONCE per module. SQLAlchemy can't un-map a
    relationship, so (like the real app) the mapping stays for the process;
    the high CC_ID keeps it clear of anything other tests set up."""
    from cps import db

    if CC_ID not in db.cc_classes:
        db.CalibreDB.setup_db_cc_classes(
            [SimpleNamespace(id=CC_ID, datatype="bool")])
    return db.cc_classes[CC_ID]


@pytest.fixture()
def harness(cc_class):
    """Real Books/ReadBook rows in fresh in-memory SQLite per test."""
    from cps import db, ub

    # metadata.db side: books + custom_column_<id>
    meta_engine = create_engine("sqlite://")
    db.Books.metadata.create_all(
        meta_engine, tables=[db.Books.__table__, cc_class.__table__])
    MetaSession = sessionmaker(bind=meta_engine)
    meta_session = MetaSession()
    for book_id in (BOOK_READING, BOOK_FINISHED, BOOK_UNTOUCHED,
                    BOOK_READ_STALE, BOOK_OTHER_USER):
        meta_session.execute(
            db.Books.__table__.insert().values(
                id=book_id, title=f"b{book_id}", sort=f"b{book_id}",
                author_sort="a", uuid=f"u{book_id}", series_index=1.0,
                path=f"p/{book_id}", has_cover=0))
    for book_id in (BOOK_FINISHED, BOOK_READ_STALE):
        meta_session.add(cc_class(book=book_id, value=True))
    meta_session.commit()

    # app.db side: ReadBook rows
    app_engine = create_engine("sqlite://")
    ub.ReadBook.metadata.create_all(app_engine, tables=[ub.ReadBook.__table__])
    AppSession = sessionmaker(bind=app_engine)
    app_session = AppSession()
    app_session.add_all([
        ub.ReadBook(user_id=USER_A, book_id=BOOK_READING,
                    read_status=ub.ReadBook.STATUS_IN_PROGRESS),
        ub.ReadBook(user_id=USER_A, book_id=BOOK_FINISHED,
                    read_status=ub.ReadBook.STATUS_FINISHED),
        ub.ReadBook(user_id=USER_A, book_id=BOOK_READ_STALE,
                    read_status=ub.ReadBook.STATUS_IN_PROGRESS),
        ub.ReadBook(user_id=USER_B, book_id=BOOK_OTHER_USER,
                    read_status=ub.ReadBook.STATUS_IN_PROGRESS),
    ])
    app_session.commit()

    with mock.patch.object(ub, "session", app_session):
        import cps
        with mock.patch.object(cps.config, "config_read_column", CC_ID,
                               create=True):
            yield SimpleNamespace(meta_session=meta_session)

    app_session.close()
    meta_session.close()


def _matching_ids(harness, value, operator="equal", user_id=USER_A):
    from cps import db
    from cps.magic_shelf import build_filter_from_rule

    rule = {"id": "read_status", "field": "read_status", "type": "integer",
            "input": "radio", "operator": operator, "value": value}
    condition = build_filter_from_rule(rule, user_id=user_id)
    assert condition is not None, (
        "read_status rule must produce a filter in custom-column mode")
    rows = harness.meta_session.query(db.Books.id).filter(condition).all()
    return sorted(r.id for r in rows)


class TestCurrentlyReadingWithCustomColumn:
    def test_in_progress_matches_only_the_reading_book(self, harness):
        """THE #634 symptom: value=2 used to collapse to bool(2)=True and
        return every custom-column-read book while missing the in-progress
        one. It must return exactly the ReadBook IN_PROGRESS books that are
        not marked read via the column."""
        assert _matching_ids(harness, 2) == [BOOK_READING]

    def test_in_progress_excludes_books_marked_read_via_column(self, harness):
        """Marking a book read via the column never clears ub.ReadBook, so a
        stale IN_PROGRESS row must not resurface a read book in the shelf."""
        assert BOOK_READ_STALE not in _matching_ids(harness, 2)

    def test_in_progress_is_scoped_to_the_user(self, harness):
        assert BOOK_OTHER_USER not in _matching_ids(harness, 2)
        assert _matching_ids(harness, 2, user_id=USER_B) == [BOOK_OTHER_USER]

    def test_in_progress_without_user_id_fails_closed(self, harness):
        from cps.magic_shelf import build_filter_from_rule
        rule = {"id": "read_status", "field": "read_status",
                "operator": "equal", "value": 2}
        assert build_filter_from_rule(rule, user_id=None) is None

    def test_read_still_matches_column_marked_books(self, harness):
        """value=1 keeps its pre-fix meaning: books with a truthy cc row."""
        assert _matching_ids(harness, 1) == [BOOK_FINISHED, BOOK_READ_STALE]

    def test_unread_includes_books_with_no_column_row(self, harness):
        """'Yet to Read' (value=0) used to match only value == False rows —
        a book never touched (no row at all) was invisible. Absence of a
        truthy row IS the unread state."""
        ids = _matching_ids(harness, 0)
        assert BOOK_UNTOUCHED in ids
        assert BOOK_FINISHED not in ids

    def test_not_equal_negates(self, harness):
        assert BOOK_READING not in _matching_ids(harness, 2, operator="not_equal")
        assert BOOK_FINISHED in _matching_ids(harness, 2, operator="not_equal")


class TestDetailPagePillCustomColumnOverlay:
    """show_book reaches deep into the request stack, so pin the overlay at
    source level (same convention as test_magic_shelf_currently_reading)."""

    def _src(self):
        from cps import web
        return inspect.getsource(web.show_book)

    def test_show_book_branches_on_read_column(self):
        src = self._src()
        assert "config.config_read_column" in src, (
            "show_book must branch on config_read_column: the raw column "
            "value is a boolean and read_status_raw derived from it can "
            "never be 2, hiding the currently-reading badge (fork #634)")

    def test_show_book_overlays_in_progress_from_readbook(self):
        src = self._src()
        assert "ub.ReadBook.STATUS_IN_PROGRESS" in src, (
            "show_book must overlay STATUS_IN_PROGRESS from ub.ReadBook in "
            "custom-column mode so the detail badge (fork #509) renders")
        assert "read_status_raw" in src
