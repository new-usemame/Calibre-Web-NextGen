# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The "Recent" library order: what this user has been reading, then the rest.

The operator's request was one sentence — "recently read first, then the books
you haven't read, newest added". The work is in *recently read*, because this
build records reading activity in several places and only some of them mean a
human opened the book.

Reading activity, enumerated from the write paths:

* ``ub.ReadBook.last_modified`` / ``.last_time_started_reading`` — written by
  ``kosync.update_book_read_status`` (KOReader sync AND the web reader, which
  reuses it), by ``kobo.HandleStateRequest``'s StatusInfo branch, and by
  ``helper.edit_book_read_status`` (the Read checkmark).
* ``ub.KoboBookmark.progress_percent`` / ``.last_modified`` / ``.created_at`` —
  the cross-device position carrier, advanced by
  ``device_reading_position.advance_kobo_bookmark`` from a Kobo state PUT, a
  KOReader sync and a web-reader save alike.
* ``ub.Bookmark.updated_at`` — the web reader's own epub.js position, stamped
  by ``cps/api/reader.py::save_bookmark`` on every save (including a save that
  carries no percentage, or one whose percentage loses furthest-wins).

The trap these tests exist for: a *row* is not evidence of reading.
``kobo.HandleStateRequest`` calls ``get_or_create_reading_state`` on **GET** as
well as PUT, so merely listing a book on a Kobo inserts ReadBook +
KoboReadingState + KoboBookmark + KoboStatistics rows with fresh clocks. An
order keyed on row existence, or on ``KoboReadingState.last_modified``, sorts a
library by "what my Kobo asked about" instead of "what I read". Every timestamp
below is therefore gated on positive evidence — a non-unread status, or a
positive stored progress — and ``test_a_kobo_state_fetch_alone_...`` is the
test that fails if that gate is dropped.

KOSync's own ``kosync_progress`` table is deliberately NOT a fourth source: it
is keyed by file checksum in app.db while the checksums live in metadata.db,
and its PUT handler already mirrors every matched book into ReadBook and the
Kobo bookmark (``kosync.py``, "Update user's ReadBook status if we matched a
book"). ``test_koreader_sync_...`` drives that mirror.

Everything here runs against real SQL on one connection carrying both schemas —
the same ATTACH shape ``cps/db.py::setup_db`` builds in production — because
the requirement is that recency is computed *in the query*, under LIMIT, and a
mock of the session cannot show that.
"""
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import flask
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import scoped_session, sessionmaker

from cps import constants, db, ub


pytestmark = pytest.mark.unit


BASE_TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

#: book id -> days after BASE_TS it was added. Deliberately NOT in id order:
#: a library where "newest added" and "highest id" agree cannot tell the
#: date-added tiebreaker apart from the uniqueness tiebreaker, and a mutation
#: that drops the first one passes every assertion (measured).
ADDED_AFTER = {1: 2, 2: 4, 3: 1, 4: 3}
#: The plain "Newest" order this library has: [2, 4, 1, 3].
NEWEST_FIRST = sorted(ADDED_AFTER, key=lambda book: -ADDED_AFTER[book])


def expected(*promoted):
    """The read books, in the order named, then the rest by date added."""
    return list(promoted) + [b for b in NEWEST_FIRST if b not in promoted]


def _book(book_id, title, timestamp):
    book = db.Books(title, title, "Author", timestamp, timestamp, "1.0",
                    timestamp, "Author/%s" % title, 1, [], [])
    book.id = book_id
    book.uuid = "uuid-%d" % book_id
    return book


def _user(session, name):
    user = ub.User(name=name, email="%s@example.invalid" % name, password="",
                   role=constants.ROLE_USER, default_language="all")
    session.add(user)
    session.commit()
    return user


class _Library:
    """One attached SQLite connection carrying app.db and metadata.db models."""

    def __init__(self, session, reader, stranger, engine, monkeypatch):
        self.session = session
        self.reader = reader
        self.stranger = stranger
        self.engine = engine
        self.monkeypatch = monkeypatch

    def order(self, sort_param="recent", user=None):
        from cps.sort_orders import book_sort_order
        user_id = None if user is None else int(user.id)
        return book_sort_order(sort_param, user_id=user_id)

    def ids(self, sort_param="recent", user=None, limit=None):
        """Read ids the way a paged list view does: ORDER BY in SQL."""
        query = self.session.query(db.Books.id).order_by(
            *self.order(sort_param, user))
        if limit is not None:
            query = query.limit(limit)
        return [row[0] for row in query.all()]

    def page(self, user, sort_param="recent", page=1, pagesize=60):
        """The ids on one page of the list views, through the real funnel.

        ``fill_indexpage`` is what /api/v1/books, the classic library and the
        shelf lists all page through, and it is not a plain SELECT: it eagerly
        loads ``Books.authors``/``tags``/``data``/``series``/``ratings`` under a
        LIMIT, so SQLAlchemy wraps the book query in a subquery and lifts every
        ORDER BY expression into it as a *selected* column — the #1411 shape.
        It also catches whatever the query raises and logs it, so a page that
        cannot be built arrives as an empty list rather than as an error.
        """
        self.monkeypatch.setattr(db, "current_user", user)
        self.monkeypatch.setattr(db.CalibreDB, "engine", self.engine)
        self.monkeypatch.setattr(
            db.CalibreDB, "session_factory",
            scoped_session(sessionmaker(bind=self.engine)))
        self.monkeypatch.setattr(db.CalibreDB, "config", SimpleNamespace(
            config_books_per_page=60, config_random_books=4,
            config_restricted_column=0, config_read_column=0))
        series_join = (db.books_series_link,
                       db.Books.id == db.books_series_link.c.book, db.Series)
        app = flask.Flask(__name__)
        with app.test_request_context("/api/v1/books"):
            entries, _random, _pagination = db.CalibreDB().fill_indexpage(
                page, pagesize, db.Books, True, self.order(sort_param, user),
                True, 0, *series_join)
        return [entry.Books.id for entry in entries]


@pytest.fixture
def library(monkeypatch):
    engine = create_engine("sqlite://")
    event.listen(
        engine, "connect",
        lambda connection, _record: connection.execute(
            "ATTACH DATABASE ':memory:' AS calibre"),
    )
    ub.Base.metadata.create_all(engine)
    db.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", session)

    reader = _user(session, "reader")
    stranger = _user(session, "stranger")
    for book_id, days in ADDED_AFTER.items():
        session.add(_book(book_id, "Book %d" % book_id,
                          BASE_TS + timedelta(days=days)))
    session.commit()

    yield _Library(session, reader, stranger, engine, monkeypatch)

    session.close()
    engine.dispose()


# --------------------------------------------------------------------------
# Drivers — each one is a real production write path, not a hand-built row.
# --------------------------------------------------------------------------

def _web_reader_save(library, book_id, cfi="epubcfi(/6/8!/4/2)", percentage=None,
                     user=None):
    """Save a position the way the SPA reader does — through the real route."""
    from cps.api import reader as reader_mod

    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    app.add_url_rule("/api/v1/books/<int:book_id>/bookmark",
                     view_func=inspect.unwrap(reader_mod.save_bookmark),
                     methods=["POST"])
    body = {"format": "epub", "bookmark": cfi}
    if percentage is not None:
        body["percentage"] = percentage
    original = reader_mod.current_user
    reader_mod.current_user = user or library.reader
    try:
        response = app.test_client().post(
            "/api/v1/books/%d/bookmark" % book_id, json=body)
    finally:
        reader_mod.current_user = original
    assert response.status_code == 204, response.get_data(as_text=True)


def _kobo_state_fetch(library, book_id, user=None):
    """What a Kobo GET of /v1/library/<uuid>/state does before it answers."""
    import cps.kobo as kobo_mod

    original = kobo_mod.current_user
    kobo_mod.current_user = user or library.reader
    try:
        return kobo_mod.get_or_create_reading_state(book_id)
    finally:
        kobo_mod.current_user = original


def _kobo_progress_put(library, book_id, percent, device_clock, user=None):
    """The write a Kobo state PUT performs for a CurrentBookmark payload."""
    from cps.services import device_reading_position as device_positions

    state = _kobo_state_fetch(library, book_id, user=user)
    outcome = device_positions.advance_kobo_bookmark(
        state.current_bookmark,
        percent,
        location_source="span", location_type="KoboSpan",
        location_value="kobo.1.1", location_supplied=True,
        incoming_clock=device_clock,
        clock_accepts=False,
        session=library.session,
    )
    library.session.commit()
    assert outcome.accepted, "fixture did not actually store Kobo progress"


def _koreader_sync(library, book_id, percent, monkeypatch, user=None):
    """The mirror the KOSync PUT runs once a checksum resolves to a book."""
    import sys

    import cps.progress_syncing.protocols.kosync  # noqa: F401
    kosync = sys.modules["cps.progress_syncing.protocols.kosync"]
    monkeypatch.setattr(kosync.config, "config_read_column", 0, raising=False)
    kosync.update_book_read_status(user or library.reader, book_id, percent)
    library.session.commit()


def _toggle_read_checkmark(library, book_id, read_status, monkeypatch):
    """The Read checkmark, through ``helper.edit_book_read_status``."""
    from cps import helper

    monkeypatch.setattr(helper.config, "config_read_column", 0, raising=False)
    monkeypatch.setattr(helper, "current_user", library.reader, raising=False)
    assert helper.edit_book_read_status(book_id, read_status=read_status) == ""
    library.session.commit()


def _same_order(actual, wanted):
    return [str(e.compile()) for e in actual] == [str(e.compile()) for e in wanted]


def _capture_shelf_order(shelves_mod, monkeypatch, viewer):
    """Stand in for the shelf listing, recording the ORDER BY it asked for."""
    from cps.pagination import Pagination

    captured = {}

    def fill(_page, _per_page, _model, _filter, order, *_a, **_kw):
        captured["order"] = order
        return [], None, Pagination(1, 60, 0)

    shelf = ub.Shelf(name="Reading", user_id=1, is_public=1)
    ub.session.add(shelf)
    ub.session.commit()

    monkeypatch.setattr(shelves_mod.calibre_db, "fill_indexpage", fill)
    monkeypatch.setattr(shelves_mod.config, "config_books_per_page", 60,
                        raising=False)
    monkeypatch.setattr(shelves_mod.config, "config_read_column", 0,
                        raising=False)
    monkeypatch.setattr(shelves_mod, "current_user", viewer, raising=False)
    monkeypatch.setattr(shelves_mod, "check_shelf_view_permissions",
                        lambda _shelf: True)
    monkeypatch.setattr(shelves_mod, "check_shelf_edit_permissions",
                        lambda _shelf: False)
    captured["shelf_id"] = shelf.id
    return captured


def _get_shelf(shelves_mod, sort):
    app = flask.Flask(__name__)
    shelf_id = ub.session.query(ub.Shelf).one().id
    with app.test_request_context("/api/v1/shelves/%d?sort=%s" % (shelf_id, sort)):
        inspect.unwrap(shelves_mod.shelf_detail)(shelf_id)


# --------------------------------------------------------------------------
# The promise
# --------------------------------------------------------------------------

def test_default_order_without_any_reading_is_still_newest_added_first(library):
    """A library nobody has opened must look exactly like "Newest"."""
    assert library.ids(user=library.reader) == NEWEST_FIRST
    assert library.ids("new", user=library.reader) == NEWEST_FIRST


def test_a_web_reader_save_moves_that_book_to_the_top(library):
    """Reading in the browser is reading. The oldest-added book leads after it."""
    _web_reader_save(library, 3, percentage=12.5)

    assert library.ids(user=library.reader) == expected(3)
    assert library.ids("new", user=library.reader) == NEWEST_FIRST, \
        "Newest must be untouched by this change"


def test_a_web_reader_save_carrying_no_percentage_still_counts(library):
    """The reader saves a CFI on every scroll; only some carry a percentage
    (``epub.locations`` has not resolved yet, or furthest-wins rejects it).
    The user still read the book."""
    _web_reader_save(library, 3, percentage=None)

    assert library.ids(user=library.reader) == expected(3)


def test_a_kobo_progress_update_moves_that_book_to_the_top(library):
    """A position pushed from the device is the other half of the promise."""
    _kobo_progress_put(library, 3, 33.0,
                       datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc))

    assert library.ids(user=library.reader) == expected(3)


def test_a_koreader_sync_moves_that_book_to_the_top(library, monkeypatch):
    """KOReader progress reaches the library through the ReadBook mirror."""
    _koreader_sync(library, 3, 40.0, monkeypatch)

    assert library.ids(user=library.reader) == expected(3)


def test_the_read_checkmark_moves_that_book_to_the_top(library, monkeypatch):
    """Marking a book read is reading activity with no position attached."""
    _toggle_read_checkmark(library, 3, True, monkeypatch)

    assert library.ids(user=library.reader) == expected(3)


def test_books_with_activity_order_by_newest_activity_first(library, monkeypatch):
    """Ties inside the read group break on the newest activity, and the
    *order the books were read in* is not the order they were added."""
    _koreader_sync(library, 2, 10.0, monkeypatch)      # read first
    _kobo_progress_put(library, 4, 20.0,
                       datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc))
    _web_reader_save(library, 3, percentage=30.0)      # read last

    assert library.ids(user=library.reader) == expected(3, 4, 2)


def test_untouched_books_follow_every_read_book_in_date_added_order(library):
    """The second half of the sentence: then the ones you haven't read,
    newest added. Proven with the OLDEST book promoted, so "the rest" cannot
    accidentally still be the whole newest-first list — and with a library
    whose date-added order is not its id order, so the date-added tiebreaker
    cannot be confused with the uniqueness one."""
    _web_reader_save(library, 3, percentage=5.0)

    order = library.ids(user=library.reader)
    assert order[0] == 3
    assert order[1:] == [2, 4, 1]


def test_later_activity_on_an_older_book_overtakes_earlier_activity(library):
    """Recency is a moving target: re-reading an old book promotes it again."""
    _kobo_progress_put(library, 1, 50.0,
                       datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc))
    _kobo_progress_put(library, 3, 50.0,
                       datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc))
    assert library.ids(user=library.reader)[:2] == [3, 1]

    _web_reader_save(library, 1, percentage=60.0)
    assert library.ids(user=library.reader)[:2] == [1, 3]


# --------------------------------------------------------------------------
# Row existence is not evidence of reading
# --------------------------------------------------------------------------

def test_a_kobo_state_fetch_alone_does_not_make_a_book_look_read(library):
    """``HandleStateRequest`` seeds the whole ReadBook/KoboReadingState graph on
    a **GET**, with fresh clocks and no progress. A Kobo that merely syncs its
    library would otherwise reorder this list by what the device asked about.

    Seeds the two OLDEST-added books only. Seeding all four would make every
    book equally "recent", so the list would come out right by accident and the
    test would pass against an order with no evidence gate at all (measured)."""
    for book_id in (3, 1):
        assert _kobo_state_fetch(library, book_id) is not None, \
            "fixture must really seed the row graph"
    library.session.commit()

    assert library.ids(user=library.reader) == NEWEST_FIRST


def test_a_kobo_progress_of_zero_is_not_reading(library):
    """0% is what a device reports for a book it opened and closed at the
    cover, and what the classic reader emits before locations resolve."""
    state = _kobo_state_fetch(library, 3)
    state.current_bookmark.progress_percent = 0.0
    library.session.commit()

    assert library.ids(user=library.reader) == NEWEST_FIRST


def test_marking_a_book_unread_returns_it_to_the_date_added_group(library, monkeypatch):
    """"Mark unread" is the documented way to restart a book (#683): it clears
    every position carrier, so the book must leave the read group too."""
    _web_reader_save(library, 3, percentage=80.0)
    assert library.ids(user=library.reader)[0] == 3

    _toggle_read_checkmark(library, 3, False, monkeypatch)

    assert library.ids(user=library.reader) == NEWEST_FIRST


# --------------------------------------------------------------------------
# Upgraded installs: reading recorded before the clock columns existed
# --------------------------------------------------------------------------
#
# Both clocks arrived by ``ALTER TABLE … ADD COLUMN`` with no default, so every
# row written before the upgrade carries NULL in them, on purpose:
#   ub.py:4862  ALTER TABLE kobo_bookmark ADD COLUMN created_at DATETIME
#   ub.py:4881  ALTER TABLE bookmark      ADD COLUMN updated_at DATETIME
#               ("Keep old CFI timestamps unknown; future saves record their
#                own clock.")
# These are not hypothetical rows: they are what every pre-upgrade library has.

def _forget_the_clock(library, table, column):
    """Leave a row exactly as ``ADD COLUMN`` left every pre-upgrade one."""
    from sqlalchemy import text

    library.session.execute(text("UPDATE %s SET %s = NULL" % (table, column)))
    library.session.commit()


def test_a_kobo_position_saved_before_the_created_at_migration_still_counts(library):
    """``created_at`` is NULL on every bookmark older than that ALTER TABLE.
    SQLite's two-argument ``max()`` is the scalar one: one NULL argument makes
    the whole expression NULL, so an uncoalesced recency would drop the book
    out of the read group entirely — silently, for exactly the users with the
    longest reading history."""
    _kobo_progress_put(library, 3, 33.0,
                       datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc))
    _forget_the_clock(library, "kobo_bookmark", "created_at")

    assert library.ids(user=library.reader) == expected(3)


def test_a_web_position_saved_before_the_updated_at_migration_still_counts(library):
    """Same shape on the web reader's own table. The user read this book in the
    browser; the build simply did not record when yet."""
    _web_reader_save(library, 3, percentage=12.5)
    _forget_the_clock(library, "bookmark", "updated_at")

    assert library.ids(user=library.reader) == expected(3)


def test_reading_with_no_recorded_clock_ranks_below_reading_that_has_one(library):
    """Undated activity outranks a book never opened, but must not outrank a
    book read yesterday — otherwise upgrading would park the oldest rows at the
    top of everyone's library."""
    _web_reader_save(library, 3, percentage=12.5)
    _forget_the_clock(library, "bookmark", "updated_at")
    _kobo_progress_put(library, 1, 50.0,
                       datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc))

    assert library.ids(user=library.reader) == expected(1, 3)


# --------------------------------------------------------------------------
# Per user, per request
# --------------------------------------------------------------------------

def test_one_users_reading_does_not_reorder_anothers_library(library, monkeypatch):
    """The whole order is per-user state; it must not leak between accounts.

    Drives **every** carrier, because each one filters on its own user column
    and dropping any single one of those filters still leaves the other two
    per-user — a one-carrier version of this test passes against a build whose
    web-reader term ignores the viewer entirely (measured)."""
    _web_reader_save(library, 1, percentage=45.0)                    # bookmark
    _kobo_progress_put(library, 4, 45.0,                             # kobo_bookmark
                       datetime(2026, 5, 1, 9, 0, tzinfo=timezone.utc))
    _koreader_sync(library, 2, 45.0, monkeypatch)                    # book_read_link

    _web_reader_save(library, 3, percentage=45.0, user=library.stranger)

    reader_order = library.ids(user=library.reader)
    assert sorted(reader_order[:3]) == [1, 2, 4], "the reader's own three books"
    assert reader_order[3] == 3, "the book only the STRANGER read stays unread"

    assert library.ids(user=library.stranger) == expected(3)


def test_an_anonymous_request_degrades_to_newest(library):
    """Guest browsing has no reading history to sort by."""
    from cps.sort_orders import BOOK_SORT_ORDERS, book_sort_order

    _web_reader_save(library, 1, percentage=90.0)

    assert library.ids(user=None) == NEWEST_FIRST
    assert book_sort_order("recent", user_id=None) is BOOK_SORT_ORDERS["new"]


# --------------------------------------------------------------------------
# It has to be an ORDER BY, and it has to be total (#1331)
# --------------------------------------------------------------------------

def test_the_first_page_is_correct_under_a_limit(library):
    """The requirement behind "computed in the query": the endpoint pages with
    LIMIT/OFFSET, so a recency resolved in Python after the fact would return
    the newest-added book here, not the recently-read one."""
    _web_reader_save(library, 1, percentage=15.0)

    assert library.ids(user=library.reader, limit=1) == [1]


def test_paging_a_recent_list_neither_drops_nor_repeats_a_book(library):
    """#1331: every book list pages with LIMIT/OFFSET, so the order must be
    total. A bulk ingest gives every book one timestamp — the tie shape that
    made "Newest" unstable — and the unread group is one big tie by design."""
    library.session.query(db.Books).update({db.Books.timestamp: BASE_TS})
    library.session.commit()
    _web_reader_save(library, 2, percentage=25.0)

    order = library.order(user=library.reader)
    seen, page = [], 0
    while True:
        ids = [row[0] for row in library.session.query(db.Books.id)
               .order_by(*order).offset(page * 2).limit(2).all()]
        if not ids:
            break
        seen.extend(ids)
        page += 1
    assert seen == [2, 4, 3, 1]
    assert sorted(seen) == [1, 2, 3, 4]


def test_the_order_survives_a_change_of_query_plan(library):
    """The measured #1331 cause: with a tie and no unique tiebreaker, adding an
    index reverses the rows SQLite returns. A total order is immune."""
    from sqlalchemy import text

    library.session.query(db.Books).update({db.Books.timestamp: BASE_TS})
    library.session.commit()
    _kobo_progress_put(library, 3, 70.0,
                       datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc))
    before = library.ids(user=library.reader)

    library.session.execute(
        text("CREATE INDEX idx_books_timestamp ON books(timestamp)"))
    library.session.commit()

    assert library.ids(user=library.reader) == before


# --------------------------------------------------------------------------
# The query the list views actually run
# --------------------------------------------------------------------------

def test_a_page_of_the_library_spans_the_read_and_the_unread_books(library):
    """The order has to survive ``fill_indexpage``, not just a SELECT.

    The library the user sees is a page built by a query that eager-loads five
    relationships under a LIMIT, so SQLAlchemy wraps it in a subquery and every
    ORDER BY expression becomes a *selected* column of that subquery — which
    means the expression's value is decoded on the way back, for every book on
    the page. "Never read" is not a moment in time, so an order that advertises
    one for it cannot survive the first unread book on the page, and
    ``fill_indexpage`` turns the failure into an empty library rather than an
    error. Measured on a 51-book rig before the fix: ``sort=recent&per_page=5``
    returned five books and ``per_page=250`` returned none.

    The "new" leg is the instrument check: it fails if this harness cannot page
    the library at all, so a failure below can only be about *this* order.
    """
    _web_reader_save(library, 3, percentage=45.0)

    assert library.page(library.reader, "new") == NEWEST_FIRST
    assert library.page(library.reader) == expected(3)
    assert (library.page(library.reader, page=1, pagesize=2)
            + library.page(library.reader, page=2, pagesize=2)) == expected(3)


# --------------------------------------------------------------------------
# Wiring: the option reaches the endpoints, and nothing it must not reach
# --------------------------------------------------------------------------

def test_the_books_endpoint_asks_for_the_per_user_order(library, monkeypatch):
    """``/api/v1/books?sort=recent`` must hand ``fill_indexpage`` the per-user
    ORDER BY, not silently fall back to newest-first."""
    from cps.api import books as books_mod
    from cps.pagination import Pagination

    captured = {}

    def fill(_page, _per_page, _model, _filter, order, *_a, **_kw):
        captured["order"] = order
        return [], None, Pagination(1, 60, 0)

    monkeypatch.setattr(books_mod.calibre_db, "fill_indexpage", fill)
    monkeypatch.setattr(books_mod.config, "config_books_per_page", 60,
                        raising=False)
    monkeypatch.setattr(books_mod.config, "config_read_column", 0,
                        raising=False)
    monkeypatch.setattr(books_mod, "current_user", library.reader,
                        raising=False)

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?sort=recent"):
        inspect.unwrap(books_mod.list_books)()

    expected = library.order(user=library.reader)
    assert [str(e.compile()) for e in captured["order"]] == \
        [str(e.compile()) for e in expected]


def test_an_anonymous_books_request_for_recent_gets_newest(library, monkeypatch):
    """The endpoint's own degradation, at the boundary the user reaches."""
    from types import SimpleNamespace

    from cps.api import books as books_mod
    from cps.pagination import Pagination
    from cps.sort_orders import BOOK_SORT_ORDERS

    captured = {}

    def fill(_page, _per_page, _model, _filter, order, *_a, **_kw):
        captured["order"] = order
        return [], None, Pagination(1, 60, 0)

    monkeypatch.setattr(books_mod.calibre_db, "fill_indexpage", fill)
    monkeypatch.setattr(books_mod.config, "config_books_per_page", 60,
                        raising=False)
    monkeypatch.setattr(books_mod.config, "config_read_column", 0,
                        raising=False)
    monkeypatch.setattr(books_mod, "current_user",
                        SimpleNamespace(is_authenticated=False,
                                        is_anonymous=True, id=None),
                        raising=False)

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?sort=recent"):
        inspect.unwrap(books_mod.list_books)()

    assert captured["order"] is BOOK_SORT_ORDERS["new"]


def test_a_shelf_can_be_sorted_by_what_the_viewer_has_been_reading(library, monkeypatch):
    """The shelf page renders the same menu, so every option in it has to do
    something. A shelf sort the endpoint does not know silently keeps the
    manual order, which would make "Recent" look broken rather than absent."""
    from cps.api import shelves as shelves_mod

    captured = _capture_shelf_order(shelves_mod, monkeypatch, library.reader)
    _get_shelf(shelves_mod, sort="recent")

    assert _same_order(captured["order"], library.order(user=library.reader))


def test_a_shelf_sorted_by_recent_for_a_guest_keeps_its_manual_order(library, monkeypatch):
    """A public shelf browsed signed-out has no history to sort by. It falls
    back the way this view falls back — to the order the owner arranged — not
    to the catalog's newest-first."""
    from types import SimpleNamespace

    from cps.api import shelves as shelves_mod

    guest = SimpleNamespace(id=99, is_authenticated=True, is_anonymous=True)
    captured = _capture_shelf_order(shelves_mod, monkeypatch, guest)
    _get_shelf(shelves_mod, sort="recent")

    assert _same_order(captured["order"], [ub.BookShelf.order.asc()])


def test_the_anonymous_browse_guest_is_never_treated_as_a_reader(library):
    """``is_authenticated`` is True for the guest — CWNG signs it in as a real
    row (``ub.User.is_anonymous`` is ``role_anonymous()``). That row is shared
    by every visitor, so sorting by its history would show one stranger's
    reading to the next."""
    from types import SimpleNamespace

    from cps.sort_orders import viewer_id

    guest = SimpleNamespace(id=7, is_authenticated=True, is_anonymous=True)
    signed_in = SimpleNamespace(id=7, is_authenticated=True, is_anonymous=False)

    assert viewer_id(guest) is None
    assert viewer_id(signed_in) == 7


def test_a_magic_shelf_cannot_be_saved_with_the_per_user_order(library):
    """Magic shelves resolve their sort with no user in scope and cache the
    result for everyone, so "recent" must not be a shelf sort. It has to fall
    back rather than raise — a saved shelf naming it still has to render."""
    from types import SimpleNamespace

    from cps.custom_column_sort import resolve_magic_shelf_sort
    from cps.sort_orders import BOOK_SORT_ORDERS, DEFAULT_SORT

    resolved = resolve_magic_shelf_sort(
        "recent",
        SimpleNamespace(config_sortable_custom_columns=""),
        columns=[],
    )

    assert resolved.key == DEFAULT_SORT
    assert list(resolved.order_by) == BOOK_SORT_ORDERS[DEFAULT_SORT]


def test_recent_is_not_in_the_static_sort_map(library):
    """It cannot be: the map is built at import time and this order needs the
    current user. Callers that iterate the map (the #1331 invariants, the magic
    shelf allowlist) must not see it."""
    from cps.api.books import SORT_MAP
    from cps.sort_orders import BOOK_SORT_ORDERS

    assert "recent" not in BOOK_SORT_ORDERS
    assert "recent" not in SORT_MAP


# --------------------------------------------------------------------------
# It runs once per page, so it has to be a seek and not a scan
# --------------------------------------------------------------------------
#
# ``book_read_link`` and ``kobo_reading_state`` each carry UNIQUE(user_id,
# book_id) already (``uq_book_read_link_user_book``,
# ``uq_kobo_reading_state_user_book``). The two position tables carried no
# index at all, so every book on the page cost a full scan of them: the list
# endpoint's cost went with books × positions rather than with the page.

def _plan(library, statement):
    from sqlalchemy import text

    return [row[-1] for row in library.session.execute(
        text("EXPLAIN QUERY PLAN " + statement)).fetchall()]


def _recent_sql(library):
    from sqlalchemy.dialects import sqlite

    query = library.session.query(db.Books.id).order_by(
        *library.order(user=library.reader)).limit(60)
    return str(query.statement.compile(
        dialect=sqlite.dialect(),
        compile_kwargs={"literal_binds": True}))


#: The per-user tables the recency expression reaches into. ``books`` is not
#: one of them: ordering a library visits every book by definition, exactly as
#: "Newest" does.
_PER_USER_TABLES = ("book_read_link", "kobo_reading_state", "kobo_bookmark",
                    "bookmark")


def test_every_reading_lookup_is_served_by_a_stored_index(library):
    """A page of this list evaluates the recency expression once per book, so
    each lookup has to be a seek. Two ways it is not: a plain SCAN, and
    SQLite's "AUTOMATIC" index — a throwaway index it builds over the whole
    table on every statement, which is what an unindexed FK gets you
    (measured: ``SEARCH kobo_bookmark_1 USING AUTOMATIC PARTIAL COVERING
    INDEX`` before ``ix_kobo_bookmark_state`` existed)."""
    plan = _plan(library, _recent_sql(library))
    touching = [step for step in plan
                if any(table in step for table in _PER_USER_TABLES)]

    assert touching, "sanity: the plan must mention the per-user tables"
    unindexed = [step for step in touching
                 if "USING" not in step or "AUTOMATIC" in step]
    assert unindexed == [], "\n".join(plan)


def test_booting_on_a_database_that_predates_the_indexes_creates_them(tmp_path, monkeypatch):
    """The indexes only reach a *fresh* install through ``create_all``. Every
    install that already exists gets them from the boot migrator, so this runs
    the real ``migrate_Database`` against a database in the old shape rather
    than the one function — a migration that is never called is the failure
    this is for. Run twice, because it runs on every boot."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from cps import config_sql, constants

    names = ("ix_bookmark_user_book", "ix_kobo_bookmark_state")
    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path), raising=False)
    engine = create_engine("sqlite:///%s" % (tmp_path / "app.db"), future=True)
    ub.Base.metadata.create_all(engine)
    config_sql._Settings.__table__.create(engine, checkfirst=True)
    with engine.begin() as connection:
        for name in names:
            connection.execute(text("DROP INDEX IF EXISTS " + name))

    boot_session = sessionmaker(bind=engine, future=True)()
    try:
        ub.migrate_Database(boot_session)
        ub.migrate_Database(boot_session)
        present = {row[0] for row in boot_session.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'index'"))}
    finally:
        boot_session.close()
        engine.dispose()

    assert set(names) <= present
