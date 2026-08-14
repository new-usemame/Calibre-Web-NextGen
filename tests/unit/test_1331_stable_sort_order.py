# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for fork #1331 — "Newest" did not put the newest book first.

@jdaybell reported that the new UI's library sort "seems to correctly sort and
then suddenly it doesn't anymore", and that it happens after switching sort.

The cause is an ORDER BY that ties. Every book list in this app pages with
LIMIT/OFFSET, and the sort orders for ``new``/``old``/``pubnew``/``pubold``/
``modifiednew``/``modifiedold``/``seriesasc``/``seriesdesc`` named exactly one
non-unique column. SQLite is then free to return the tied rows in whatever
order its plan happens to produce — measured below: a bulk ingest that gives
twenty books one timestamp comes back *oldest-first* under "Newest", because
the planner walks the rowid; add an index on the sort column and the same query
returns the opposite order.

``abc``/``zyx`` already ended in ``Books.id`` and were unaffected, which is why
switching between sorts is what exposes it.

These tests pin the fix at the level it has to hold at: every order in the one
shared map ends in a unique tiebreaker, both UIs read that same map, and the
resulting page sequence is invariant to the query plan.
"""
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker


BULK_TS = "2026-08-01 10:00:00+00:00"
NO_DATE = "0101-01-01 00:00:00+00:00"


@pytest.fixture()
def books_engine():
    """An in-memory calibre ``books`` table carrying the tie shapes a real
    library has: a bulk ingest sharing one timestamp, calibre's no-pubdate
    sentinel, and the 1.0 series_index every standalone book gets."""
    from cps import db

    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _register(dbapi_conn, _rec):
        # ng_sort_key is registered on the app's own connections; the abc/zyx
        # orders compile to a call to it.
        dbapi_conn.create_function("ng_sort_key", 1, lambda v: (v or "").lower())

    db.Books.__table__.create(engine)
    with engine.begin() as conn:
        rows = []
        for i in range(1, 21):  # one ingest run, one timestamp
            rows.append((i, f"Bulk {i:02d}", BULK_TS, NO_DATE, 1.0, BULK_TS))
        for n, i in enumerate(range(21, 26), start=2):  # added later, one by one
            ts = f"2026-08-0{n} 12:00:00+00:00"
            rows.append((i, f"Later {i}", ts, f"20{n:02d}-01-01 00:00:00+00:00", 1.0, ts))
        for r in rows:
            conn.execute(
                text("INSERT INTO books (id, title, sort, author_sort, timestamp, "
                     "pubdate, series_index, last_modified, path, has_cover, uuid) "
                     "VALUES (:id, :t, :t, 'A', :ts, :pub, :si, :lm, '.', 0, :u)"),
                {"id": r[0], "t": r[1], "ts": r[2], "pub": r[3], "si": r[4],
                 "lm": r[5], "u": f"uuid-{r[0]}"},
            )
    return engine


def _page_ids(engine, order, per_page=10):
    """Read every id the way a list view does — one LIMIT/OFFSET query per page."""
    from cps import db

    session = sessionmaker(bind=engine)()
    try:
        out, page = [], 0
        while True:
            # The id column rather than the entity: a Books row eager-loads its
            # authors, and the link tables are not what is under test here.
            ids = [row[0] for row in session.query(db.Books.id).order_by(*order)
                   .offset(page * per_page).limit(per_page).all()]
            if not ids:
                return out
            out.extend(ids)
            page += 1
    finally:
        session.close()


# --------------------------------------------------------------------------
# The defect, stated as behaviour
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_newest_puts_the_newest_book_first_inside_a_bulk_ingest(books_engine):
    """The reporter's symptom. Twenty books share one timestamp; "Newest" must
    still read newest-added first, not walk them in insertion order."""
    from cps.api.books import SORT_MAP

    ids = _page_ids(books_engine, SORT_MAP["new"])
    assert ids == [25, 24, 23, 22, 21] + list(range(20, 0, -1))


@pytest.mark.unit
def test_oldest_is_the_exact_reverse(books_engine):
    """"Oldest" breaks the same tie the other way, so the two sorts are
    reverses of each other rather than two arbitrary orders."""
    from cps.api.books import SORT_MAP

    newest = _page_ids(books_engine, SORT_MAP["new"])
    assert _page_ids(books_engine, SORT_MAP["old"]) == list(reversed(newest))


@pytest.mark.unit
def test_page_sequence_survives_a_change_of_query_plan(books_engine):
    """The measured cause: with a tie and no tiebreaker, adding an index on the
    sort column reverses the order SQLite returns for the tied rows — the same
    query, the same data, a different plan. A total order is immune."""
    from cps.api.books import SORT_MAP

    before = _page_ids(books_engine, SORT_MAP["new"])
    with books_engine.begin() as conn:
        conn.execute(text("CREATE INDEX idx_books_timestamp ON books(timestamp)"))
    assert _page_ids(books_engine, SORT_MAP["new"]) == before


@pytest.mark.unit
def test_no_page_drops_or_repeats_a_book(books_engine):
    """Paging is LIMIT/OFFSET over the same order, so an unstable order can
    hand the same book to two pages and never show another at all."""
    from cps.api.books import SORT_MAP

    for key in ("new", "old", "pubnew", "pubold", "seriesasc", "seriesdesc"):
        ids = _page_ids(books_engine, SORT_MAP[key], per_page=7)
        assert sorted(ids) == list(range(1, 26)), f"{key} lost or repeated a book"


@pytest.mark.unit
def test_undated_books_still_have_one_definite_order(books_engine):
    """calibre stores "no publication date" as 0101-01-01, so a library of
    sideloaded books is one large tie under a pubdate sort."""
    from cps.api.books import SORT_MAP

    undated = [b for b in _page_ids(books_engine, SORT_MAP["pubnew"]) if b <= 20]
    assert undated == list(range(20, 0, -1))


@pytest.mark.unit
def test_standalone_books_still_have_one_definite_order(books_engine):
    """Every book outside a series carries series_index 1.0, so series order is
    one tie across the whole library."""
    from cps.api.books import SORT_MAP

    assert _page_ids(books_engine, SORT_MAP["seriesasc"]) == list(range(1, 26))


# --------------------------------------------------------------------------
# The rule, enumerated — so the next sort key added cannot skip it
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_every_sort_order_ends_in_a_unique_column():
    """Enumerated over the whole map, not asserted on the entries this fix
    happened to touch: a sort added later without a tiebreaker fails here."""
    from cps import db, ub
    from cps.api.books import SORT_MAP

    unique = {db.Books.__table__.c.id, ub.Downloads.__table__.c.book_id}
    for key, order in SORT_MAP.items():
        assert order, f"{key} has no ORDER BY at all"
        last = order[-1]
        column = getattr(last, "element", last)
        column = getattr(column, "__clause_element__", lambda: column)()
        assert column in unique, (
            f"sort {key!r} ends on {last}, which is not unique — its ties are "
            f"ordered by whatever plan SQLite picks (see #1331)")


@pytest.mark.unit
def test_the_hot_lists_tiebreak_on_a_column_their_query_can_see():
    """The hot lists run against the app database and group on Downloads, so
    Books.id is not in scope there — the tiebreaker has to be the group key."""
    from cps.sort_orders import BOOK_SORT_ORDERS

    for key in ("hotdesc", "hotasc"):
        assert str(BOOK_SORT_ORDERS[key][-1].element) == "downloads.book_id"


@pytest.mark.unit
def test_the_tiebreaker_runs_the_same_way_as_the_sort():
    """A descending sort breaks ties descending. Otherwise "Newest" would open
    a tie group at its oldest member — the reporter's exact complaint."""
    from cps.sort_orders import BOOK_SORT_ORDERS

    descending = {"new", "pubnew", "modifiednew", "zyx", "authza", "seriesdesc", "hotdesc"}
    for key, order in BOOK_SORT_ORDERS.items():
        is_desc = "DESC" in str(order[-1].compile()).upper()
        assert is_desc == (key in descending), f"{key} tiebreaks against its own direction"


# --------------------------------------------------------------------------
# One map, two UIs
# --------------------------------------------------------------------------

@pytest.mark.unit
def test_the_api_reads_the_shared_map():
    """The /api/v1 lists must not carry a second copy that can drift."""
    from cps.api.books import SORT_MAP
    from cps.sort_orders import BOOK_SORT_ORDERS

    assert SORT_MAP is BOOK_SORT_ORDERS


@pytest.mark.unit
def test_the_classic_ui_reads_the_shared_map():
    """The classic UI had the identical defect — same missing tiebreakers, same
    paging. It resolves through the same map now, so neither can be fixed
    without the other."""
    from unittest.mock import patch

    from cps import web
    from cps.sort_orders import BOOK_SORT_ORDERS

    with patch.object(web, "current_user") as user:
        user.get_view_property.return_value = "new"
        for key, expected in BOOK_SORT_ORDERS.items():
            order, resolved = web.get_sort_function(key, "newest")
            assert resolved == key
            assert [str(e.compile()) for e in order] == [str(e.compile()) for e in expected]


@pytest.mark.unit
def test_an_unknown_sort_still_falls_back_to_newest():
    """Behaviour that predates this change and must survive it."""
    from unittest.mock import patch

    from cps import web
    from cps.sort_orders import BOOK_SORT_ORDERS
    from cps.api.books import SORT_MAP

    assert SORT_MAP.get("nonsense", SORT_MAP["new"]) is BOOK_SORT_ORDERS["new"]
    with patch.object(web, "current_user") as user:
        user.get_view_property.return_value = "new"
        order, _ = web.get_sort_function("nonsense", "newest")
        assert [str(e.compile()) for e in order] == \
            [str(e.compile()) for e in BOOK_SORT_ORDERS["new"]]


@pytest.mark.unit
def test_a_series_page_with_no_chosen_sort_still_opens_in_series_order():
    """#573/#334 behaviour, kept: a series reads 1, 2, 3 rather than newest-first."""
    from unittest.mock import patch

    from cps import web
    from cps.sort_orders import BOOK_SORT_ORDERS

    with patch.object(web, "current_user"):
        order, resolved = web.get_sort_function(None, "series")
    assert resolved == "seriesasc"
    assert [str(e.compile()) for e in order] == \
        [str(e.compile()) for e in BOOK_SORT_ORDERS["seriesasc"]]
