# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for fork #1702's SPA library-card Reading badge.

The detail endpoint already exposes the sync-driven tri-state, but the shared
list endpoint flattened it to ``read``/``unread``.  These tests drive the real
``list_books`` view serialization and a real in-memory ``ub.ReadBook`` table so
the custom-column overlay and its query budget are observable together.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


pytestmark = pytest.mark.unit


def _book(book_id: int):
    return SimpleNamespace(
        id=book_id,
        title=f"Book {book_id}",
        series_index=1.0,
        has_cover=0,
        authors=[],
        series=[],
        data=[],
        tags=[],
    )


@pytest.fixture
def readbook_db():
    from cps import ub

    engine = create_engine("sqlite:///:memory:")
    ub.ReadBook.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield ub, engine, session
    finally:
        session.close()
        engine.dispose()


def _list_response(*, rows, read_column, user, ub_session):
    from cps.api import books as books_mod
    from cps.pagination import Pagination

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books?page=1"):
        with patch.object(
            books_mod.calibre_db,
            "fill_indexpage",
            return_value=(rows, None, Pagination(1, 60, len(rows))),
        ), patch.object(
            books_mod.config, "config_books_per_page", 60, create=True
        ), patch.object(
            books_mod.config, "config_read_column", read_column, create=True
        ), patch.object(
            books_mod, "current_user", user
        ), patch.object(
            books_mod.ub, "session", ub_session
        ):
            response = inspect.unwrap(books_mod.list_books)()
    return json.loads(response.get_data(as_text=True))


@pytest.mark.parametrize("read_column", [0, 1702], ids=["built-in", "custom-column"])
def test_list_endpoint_exposes_in_progress_for_only_the_reading_book(
    readbook_db, read_column
):
    """Reading/read/untouched remain distinct in both read configurations.

    The custom-column case deliberately gives the finished book a stale
    IN_PROGRESS ``ReadBook`` row.  Its truthy custom value must win, matching
    ``book_is_in_progress`` and preventing simultaneous Read/Reading badges.
    """
    ub, engine, session = readbook_db
    user = SimpleNamespace(id=9, is_authenticated=True, is_anonymous=False)

    if read_column:
        rows = [
            SimpleNamespace(Books=_book(1), is_archived=False, value=False),
            SimpleNamespace(Books=_book(2), is_archived=False, value=True),
            SimpleNamespace(Books=_book(3), is_archived=False, value=False),
        ]
        session.add_all([
            ub.ReadBook(user_id=9, book_id=1, read_status=ub.ReadBook.STATUS_IN_PROGRESS),
            # Stale sync state beneath an authoritative finished custom value.
            ub.ReadBook(user_id=9, book_id=2, read_status=ub.ReadBook.STATUS_IN_PROGRESS),
        ])
        session.commit()
        expected_queries = 1
    else:
        rows = [
            SimpleNamespace(
                Books=_book(1), is_archived=False,
                read_status=ub.ReadBook.STATUS_IN_PROGRESS,
            ),
            SimpleNamespace(
                Books=_book(2), is_archived=False,
                read_status=ub.ReadBook.STATUS_FINISHED,
            ),
            SimpleNamespace(
                Books=_book(3), is_archived=False,
                read_status=ub.ReadBook.STATUS_UNREAD,
            ),
        ]
        expected_queries = 0

    statements = []

    def count_statement(*args):
        statements.append(args[2])

    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        body = _list_response(
            rows=rows,
            read_column=read_column,
            user=user,
            ub_session=session,
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    assert [item["in_progress"] for item in body["items"]] == [True, False, False]
    assert [item["read"] for item in body["items"]] == [False, True, False]
    assert len(statements) == expected_queries, (
        f"page serialization issued {len(statements)} app.db queries; "
        f"expected {expected_queries}: {statements}"
    )


def test_anonymous_list_never_queries_or_exposes_in_progress(readbook_db):
    """Anonymous browse fails closed before touching the per-user table."""
    ub, engine, session = readbook_db
    session.add(ub.ReadBook(
        user_id=9, book_id=1, read_status=ub.ReadBook.STATUS_IN_PROGRESS
    ))
    session.commit()
    rows = [SimpleNamespace(Books=_book(1), is_archived=False, value=False)]
    anonymous = SimpleNamespace(id=9, is_authenticated=False, is_anonymous=True)
    statements = []

    def count_statement(*args):
        statements.append(args[2])

    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        body = _list_response(
            rows=rows,
            read_column=1702,
            user=anonymous,
            ub_session=session,
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    assert body["items"][0]["in_progress"] is False
    assert statements == []
