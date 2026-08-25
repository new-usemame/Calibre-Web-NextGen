# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for #1677's mixed-format Kobo timestamp cursor.

Calibre writes ``books.last_modified`` with a UTC suffix, while SQLAlchemy's
SQLite binder writes the cursor without one.  These tests deliberately seed the
column as literal TEXT through raw SQL; assigning a Python datetime through the
ORM would erase the production shape and make the broken predicate pass.
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from cps import db
from cps.kobo import (
    books_cursor_datetime,
    books_keyset_after_cursor,
    normalized_books_last_modified,
)
from cps.services.SyncToken import SyncToken


@pytest.fixture
def book_session():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE books (id INTEGER PRIMARY KEY, last_modified TIMESTAMP NOT NULL)"
        )
    with Session(engine) as session:
        yield session
    engine.dispose()


def _seed_literal(session, rows):
    for book_id, timestamp_text in rows:
        session.connection().exec_driver_sql(
            "INSERT INTO books (id, last_modified) VALUES (?, ?)",
            (book_id, timestamp_text),
        )
    session.commit()


def _sync_once(session, incoming_header=None, limit=100):
    headers = {SyncToken.SYNC_TOKEN_HEADER: incoming_header} if incoming_header else {}
    token = SyncToken.from_headers(headers)
    rows = (
        session.query(db.Books.id, db.Books.last_modified)
        .filter(books_keyset_after_cursor(token.books_last_modified, token.books_last_id))
        .order_by(normalized_books_last_modified(db.Books.last_modified), db.Books.id)
        .limit(limit)
        .all()
    )
    response_entries = [{"ChangedEntitlement": {"BookId": row.id}} for row in rows]
    if rows:
        token.books_last_modified = books_cursor_datetime(rows[-1].last_modified)
        token.books_last_id = rows[-1].id
    return response_entries, token.build_sync_token()


@pytest.mark.unit
def test_suffixed_fractional_tip_is_not_reemitted(book_session):
    _seed_literal(book_session, [(1, "2026-08-15 16:50:40.995056+00:00")])

    first, returned_token = _sync_once(book_session)
    second, _ = _sync_once(book_session, returned_token)

    assert [entry["ChangedEntitlement"]["BookId"] for entry in first] == [1]
    assert second == []


@pytest.mark.unit
def test_suffixed_whole_second_is_delivered_not_skipped(book_session):
    _seed_literal(book_session, [(1, "2026-05-03 14:40:55+00:00")])
    cursor = SyncToken(
        books_last_modified=datetime(2026, 5, 3, 14, 40, 55),
        books_last_id=-1,
    ).build_sync_token()

    entries, _ = _sync_once(book_session, cursor)

    assert [entry["ChangedEntitlement"]["BookId"] for entry in entries] == [1]


@pytest.mark.unit
def test_mixed_timestamp_shapes_deliver_once_and_terminate(book_session):
    _seed_literal(book_session, [
        (1, "2026-05-03 14:40:55+00:00"),
        (2, "2026-05-03 14:40:55.000001"),
        (3, "2026-08-15 16:50:40.995055"),
        (4, "2026-08-15 16:50:40.995056+00:00"),
        (5, "2026-08-15 16:50:40.995057"),
    ])

    delivered = []
    token = None
    for _ in range(10):
        entries, token = _sync_once(book_session, token, limit=2)
        if not entries:
            break
        delivered.extend(entry["ChangedEntitlement"]["BookId"] for entry in entries)
    else:
        pytest.fail("mixed-format keyset did not terminate within ten syncs")

    assert delivered == [1, 2, 3, 4, 5]
    assert len(delivered) == len(set(delivered))


@pytest.mark.unit
def test_suffixed_row_that_is_not_the_maximum_drains_forward(book_session):
    """A suffixed row the cursor comes to REST on must not trap the walk.

    The tip case is the one users report, but it is not the only way this
    predicate fails.  Measured on a real library: the sync drains forward past
    intermediate suffixed rows because a later row pushes the cursor beyond
    them, so their broken equality arm is never consulted -- and only the row
    holding the maximum, with nothing after it to rescue it, loops.  A future
    change that breaks forward-draining would reintroduce re-downloads on a
    different set of books, and a suite that only asserts the tip would stay
    green.  ``limit=1`` forces the cursor to rest exactly on the suffixed
    middle row, which is the state the aggregate mixed-shapes test only reaches
    incidentally.
    """
    _seed_literal(book_session, [
        (1, "2026-05-03 14:40:55.000001"),
        (2, "2026-07-08 03:34:24.752033+00:00"),   # suffixed, NOT the maximum
        (3, "2026-08-15 16:50:40.995056"),
    ])

    delivered, token = [], None
    for _ in range(8):
        entries, token = _sync_once(book_session, token, limit=1)
        if not entries:
            break
        delivered.extend(entry["ChangedEntitlement"]["BookId"] for entry in entries)
    else:
        pytest.fail(
            "walk did not terminate: the cursor is trapped on the suffixed "
            "middle row instead of draining forward past it"
        )

    assert delivered == [1, 2, 3]


@pytest.mark.unit
def test_normalized_predicate_treats_suffixed_storage_as_cursor_equal(book_session):
    _seed_literal(book_session, [(7, "2026-08-15 16:50:40.995056+00:00")])
    cursor = datetime(2026, 8, 15, 16, 50, 40, 995056)

    stored_equals_cursor = (
        book_session.query(db.Books.id)
        .filter(
            normalized_books_last_modified(db.Books.last_modified)
            == normalized_books_last_modified(cursor)
        )
        .one()
    )

    assert stored_equals_cursor.id == 7


@pytest.mark.unit
@pytest.mark.parametrize(("stored_text", "cursor"), [
    ("2026-08-15 16:50:40.500", datetime(2026, 8, 15, 16, 50, 40, 500000)),
    ("2026-08-15 16:50:40.500+00:00", datetime(2026, 8, 15, 16, 50, 40, 500000)),
    ("2026-08-15 16:50:40.5000000+00:00", datetime(2026, 8, 15, 16, 50, 40, 500000)),
    ("2026-08-15 16:50:40.5", datetime(2026, 8, 15, 16, 50, 40, 500000)),
])
def test_variable_width_fraction_matches_cursor_and_does_not_reemit(
    book_session, stored_text, cursor
):
    _seed_literal(book_session, [(9, stored_text)])

    stored_equals_cursor = (
        book_session.query(db.Books.id)
        .filter(
            normalized_books_last_modified(db.Books.last_modified)
            == normalized_books_last_modified(cursor)
        )
        .one()
    )
    first, returned_token = _sync_once(book_session)
    second, _ = _sync_once(book_session, returned_token)

    assert stored_equals_cursor.id == 9
    assert [entry["ChangedEntitlement"]["BookId"] for entry in first] == [9]
    assert second == []
