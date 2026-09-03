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
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker


pytestmark = pytest.mark.unit

API_DIR = Path(__file__).resolve().parents[2] / "cps" / "api"


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
        # One read-status lookup plus one bulk personal-cover lookup. The
        # latter stays one query for the page, never one per book.
        expected_queries = 2
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
        # Cover preferences are app.db state and resolve once per page.
        expected_queries = 1

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


def test_custom_read_column_chunks_oversized_in_progress_lookup(readbook_db):
    """An uncapped page must stay below SQLite's conservative bind ceiling."""
    ub, engine, session = readbook_db
    user = SimpleNamespace(id=9, is_authenticated=True, is_anonymous=False)
    # 901 eligible ids crosses the production 900-id chunk boundary. Only the
    # first and last are reading so the result also proves chunks are combined.
    statuses = [(book_id, False) for book_id in range(1, 902)]
    session.add_all([
        ub.ReadBook(user_id=9, book_id=1, read_status=ub.ReadBook.STATUS_IN_PROGRESS),
        ub.ReadBook(user_id=9, book_id=901, read_status=ub.ReadBook.STATUS_IN_PROGRESS),
    ])
    session.commit()
    statements = []
    parameter_counts = []

    def capture_statement(*args):
        statements.append(args[2])
        parameter_counts.append(len(args[3]))

    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        from cps.helper import book_in_progress_ids

        with patch.object(ub, "session", session):
            result = book_in_progress_ids(statuses, True, user)
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert result == {1, 901}
    assert len(statements) == 2, "901 ids must execute as 900 + 1, not one oversized IN"
    # Two scalar predicates (user/status) accompany each IN list.
    assert sorted(parameter_counts) == [3, 902]
    assert all("book_id IN" in statement for statement in statements)


def test_currently_reading_magic_shelf_items_expose_in_progress():
    """The built-in Currently Reading shelf must not lose its own state badge."""
    from cps import ub
    from cps.api import books as books_mod
    from cps.api import magicshelves as magic_mod
    from cps.pagination import Pagination

    user = SimpleNamespace(id=9, is_authenticated=True, is_anonymous=False)
    shelf = SimpleNamespace(
        id=17,
        name="Currently Reading",
        icon="book",
        user_id=9,
        is_public=False,
        is_system=True,
        kobo_sync=False,
        rules={"condition": "AND", "rules": []},
    )
    row = SimpleNamespace(
        Books=_book(1),
        is_archived=False,
        read_status=ub.ReadBook.STATUS_IN_PROGRESS,
    )
    app_session = MagicMock()
    app_session.query.return_value.get.return_value = shelf

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/magicshelf/17"):
        with patch.object(magic_mod.ub, "session", app_session), patch.object(
            magic_mod, "current_user", user
        ), patch.object(
            books_mod, "current_user", user
        ), patch.object(
            magic_mod.config, "config_books_per_page", 60, create=True
        ), patch.object(
            magic_mod.config, "config_read_column", 0, create=True
        ), patch.object(
            magic_mod.magic_shelf, "build_query_from_rules", return_value=True
        ), patch.object(
            magic_mod.magic_shelf,
            "system_magic_shelf_display_name",
            return_value="Currently Reading",
        ), patch.object(
            magic_mod.calibre_db,
            "fill_indexpage",
            return_value=([row], None, Pagination(1, 60, 1)),
        ):
            response = inspect.unwrap(magic_mod.magic_shelf_books)(17)

    body = json.loads(response.get_data(as_text=True))
    assert body["items"][0]["in_progress"] is True
    assert body["items"][0]["read"] is False


def test_shelf_detail_items_expose_in_progress():
    """Ordinary shelf cards carry the same tri-state as the library grid."""
    from cps import ub
    from cps.api import books as books_mod
    from cps.api import shelves as shelves_mod
    from cps.pagination import Pagination

    user = SimpleNamespace(id=9, is_authenticated=True, is_anonymous=False)
    shelf = SimpleNamespace(
        id=23, name="To read next", user_id=9, is_public=False, kobo_sync=False
    )
    row = SimpleNamespace(
        Books=_book(2),
        is_archived=False,
        read_status=ub.ReadBook.STATUS_IN_PROGRESS,
    )
    app_session = MagicMock()
    app_session.query.return_value.filter.return_value.first.return_value = shelf

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/shelves/23"):
        with patch.object(shelves_mod.ub, "session", app_session), patch.object(
            shelves_mod, "current_user", user
        ), patch.object(
            books_mod, "current_user", user
        ), patch.object(
            shelves_mod.config, "config_books_per_page", 60, create=True
        ), patch.object(
            shelves_mod.config, "config_read_column", 0, create=True
        ), patch.object(
            shelves_mod, "check_shelf_view_permissions", return_value=True
        ), patch.object(
            shelves_mod, "check_shelf_edit_permissions", return_value=True
        ), patch.object(
            shelves_mod.calibre_db,
            "fill_indexpage",
            return_value=([row], None, Pagination(1, 60, 1)),
        ):
            response = inspect.unwrap(shelves_mod.shelf_detail)(23)

    body = json.loads(response.get_data(as_text=True))
    assert body["items"][0]["in_progress"] is True
    assert body["items"][0]["read"] is False


def test_advanced_search_items_expose_in_progress():
    """Advanced-search cards carry the same tri-state as the library grid."""
    from cps import ub
    from cps.api import books as books_mod
    from cps.api import search as search_mod

    user = SimpleNamespace(id=9, is_authenticated=True, is_anonymous=False)
    row = SimpleNamespace(
        Books=_book(3),
        is_archived=False,
        read_status=ub.ReadBook.STATUS_IN_PROGRESS,
    )
    query = MagicMock()
    query.distinct.return_value = query
    query.order_by.return_value = query
    query.count.return_value = 1
    query.offset.return_value = query
    query.limit.return_value = query
    query.all.return_value = [row]

    app = flask.Flask(__name__)
    with app.test_request_context(
        "/api/v1/search/advanced", method="POST", json={"title": "Book"}
    ):
        with patch.object(search_mod, "current_user", user), patch.object(
            books_mod, "current_user", user
        ), patch.object(
            search_mod.config, "config_books_per_page", 60, create=True
        ), patch.object(
            search_mod.config, "config_read_column", 0, create=True
        ), patch.object(
            search_mod, "build_adv_search_query", return_value=(query, "")
        ):
            response = inspect.unwrap(search_mod.advanced_search)()

    body = json.loads(response.get_data(as_text=True))
    assert body["items"][0]["in_progress"] is True
    assert body["items"][0]["read"] is False


def test_no_api_list_surface_bypasses_batch_read_state_resolution():
    """A new endpoint cannot copy the old single-row serialization mistake."""
    offenders = sorted(
        path.name
        for path in API_DIR.glob("*.py")
        if path.name != "books.py" and "_row_to_item" in path.read_text(encoding="utf-8")
    )
    assert offenders == [], (
        "API list surfaces must call _rows_to_items so in-progress state is "
        f"resolved once for the page; direct _row_to_item users: {offenders}"
    )


def test_single_row_serializer_requires_a_resolved_in_progress_set():
    """Even books.py callers must not silently default every item to false."""
    from cps.api.books import _row_to_item

    parameter = inspect.signature(_row_to_item).parameters["in_progress_ids"]
    assert parameter.default is inspect.Parameter.empty
