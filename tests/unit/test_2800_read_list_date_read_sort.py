# SPDX-License-Identifier: GPL-3.0-or-later
"""#2800 -- the new UI's Read list could not be sorted by when a book was read.

"Recent" puts finished books back in date-added order, so a book finished today
that was added a year ago sat far down the list. ``readnew``/``readold`` rank
the finished books by ``book_read_link.read_status_choice_at`` (falling back to
``last_modified`` for rows from before that clock), per reader.

These run the real ORDER BY against real tables, not the source text.
"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from cps import db, ub
from cps.sort_orders import READ_DATE_SORTS, book_sort_order


def _ids(session, sort, user_id):
    query = select(db.Books.id).order_by(*book_sort_order(sort, user_id=user_id))
    return list(session.execute(query).scalars())


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    db.Books.__table__.create(engine)
    ub.ReadBook.__table__.create(engine)
    with sessionmaker(bind=engine)() as s:
        # Added oldest -> newest: 1, 2, 3, 4, 5.
        for i in range(1, 6):
            ts = f"2020-01-0{i} 00:00:00+00:00"
            s.execute(text(
                "INSERT INTO books (id, title, sort, author_sort, timestamp, pubdate, "
                "series_index, last_modified, path, has_cover, uuid) "
                "VALUES (:id, :t, :t, 'A', :ts, :ts, 1.0, :ts, '.', 0, :u)"),
                {"id": i, "t": f"B{i}", "ts": ts, "u": f"u{i}"})
        s.commit()

        def row(user, book, status, choice_at, modified=datetime(2024, 1, 1)):
            s.add(ub.ReadBook(user_id=user, book_id=book, read_status=status,
                              read_status_choice_at=choice_at, last_modified=modified))

        F, P = ub.ReadBook.STATUS_FINISHED, ub.ReadBook.STATUS_IN_PROGRESS
        row(1, 1, F, datetime(2026, 10, 30))                       # oldest added, finished today-ish
        row(1, 2, F, datetime(2026, 1, 5))
        row(1, 3, F, None, modified=datetime(2025, 6, 1))          # pre-clock row: falls back
        row(1, 4, P, datetime(2026, 11, 1))                        # in progress: not a read date
        row(2, 5, F, datetime(2026, 11, 2))                        # someone else's reading
        s.commit()
        yield s


@pytest.mark.unit
def test_newest_read_first_regardless_of_date_added(session):
    # 1 finished 30/10/2026, 3 falls back to 1/6/2025... 2 finished 5/01/2026.
    assert _ids(session, "readnew", 1)[:3] == [1, 2, 3]


@pytest.mark.unit
def test_oldest_read_first_is_the_reverse_of_the_finished_group(session):
    assert _ids(session, "readold", 1)[:3] == [3, 2, 1]


@pytest.mark.unit
def test_unfinished_books_follow_in_date_added_order_in_both_directions(session):
    # 4 (in progress), 5 (only user 2 finished it): never ranked by a read date.
    assert _ids(session, "readnew", 1)[3:] == [5, 4]
    assert _ids(session, "readold", 1)[3:] == [5, 4]


@pytest.mark.unit
def test_another_readers_finish_date_is_not_used(session):
    assert _ids(session, "readnew", 2)[0] == 5


@pytest.mark.unit
def test_no_viewer_degrades_to_newest_added(session):
    assert _ids(session, "readnew", None) == [5, 4, 3, 2, 1]


@pytest.mark.unit
def test_the_api_accepts_the_read_date_sorts():
    from cps.api.books import _COMPATIBLE_BOOK_SORTS
    assert READ_DATE_SORTS <= _COMPATIBLE_BOOK_SORTS
