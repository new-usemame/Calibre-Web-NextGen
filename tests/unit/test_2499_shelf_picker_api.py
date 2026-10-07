# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2499: the new UI's shelf "Add books" picker endpoint.

``GET /api/v1/shelves/<id>/available-books`` shares its query with the classic
modal (``cps.shelf.shelf_picker_books``). These tests drive the real view over
an in-memory database: only books the caller may browse are listed, books
already on the shelf are flagged, paging reports what is left, and a shelf the
caller cannot edit lists nothing at all.
"""

from datetime import datetime, timedelta, timezone
import inspect

import pytest
from flask import Flask
from sqlalchemy import create_engine, event, func, literal
from sqlalchemy.orm import sessionmaker

from cps import constants, db, ub


pytestmark = pytest.mark.unit

VISIBLE_IDS = list(range(1, 36))   # 35 browsable books: more than one page
HIDDEN_ID = 99                     # outside the caller's visibility filter


def _book(book_id):
    added = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=book_id)
    title = "Book %d" % book_id
    book = db.Books(title, title, "Author", added, db.Books.DEFAULT_PUBDATE,
                    "1.0", added, "book-%d" % book_id, 0, [], [])
    book.id = book_id
    book.uuid = "book-uuid-%d" % book_id
    return book


@pytest.fixture
def picker(monkeypatch):
    from cps import shelf as shelf_module
    from cps.api import shelves as shelves_api

    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda connection, _record: connection.execute(
        "ATTACH DATABASE ':memory:' AS calibre"))
    ub.Base.metadata.create_all(engine)
    db.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    owner = ub.User(name="owner", email="owner@example.invalid", password="",
                    role=constants.ROLE_USER, default_language="all")
    other = ub.User(name="other", email="other@example.invalid", password="",
                    role=constants.ROLE_USER, default_language="all")
    session.add_all([owner, other])
    session.flush()
    mine = ub.Shelf(id=9, name="Mine", is_public=0, user_id=owner.id)
    session.add_all([mine, ub.Shelf(id=10, name="Theirs", is_public=0, user_id=other.id)])
    session.add_all([_book(i) for i in VISIBLE_IDS + [HIDDEN_ID]])
    mine.books.append(ub.BookShelf(book_id=35, order=1))
    session.commit()

    searches = []

    def search_query(term, _config, eager_data=True):
        # Like the real search: rows wrap the book at `.Books`, and the caller's
        # visibility is NOT applied here (the real one allows archived books).
        searches.append(term)
        return (session.query(db.Books, literal(1).label("read_status"))
                .filter(func.lower(db.Books.title).like("%" + term.lower() + "%")))

    cdb = type("TestCalibreDB", (), {
        "session": session,
        "common_filters": staticmethod(lambda **_kw: db.Books.id != HIDDEN_ID),
        "search_query": staticmethod(search_query),
    })()
    monkeypatch.setattr(ub, "session", session)
    monkeypatch.setattr(shelf_module, "calibre_db", cdb)
    monkeypatch.setattr(shelf_module, "current_user", owner)
    monkeypatch.setattr(shelves_api, "current_user", owner)
    monkeypatch.setattr(shelves_api, "cover_url_for",
                        lambda book, res, cover_override=None: cover_override or "/cover/%d/%s" % (book.id, res))
    monkeypatch.setattr(shelves_api.user_cover, "overrides_for_user",
                        lambda user_id, ids: {34: "/user-cover/34"} if user_id == owner.id else {})

    app = Flask(__name__)
    app.add_url_rule("/api/v1/shelves/<int:shelf_id>/available-books",
                     view_func=inspect.unwrap(shelves_api.shelf_available_books_api))
    app.add_url_rule("/shelf/<int:shelf_id>/available_books",
                     view_func=inspect.unwrap(shelf_module.shelf_available_books))
    app.add_url_rule("/cover/<int:book_id>/<resolution>", endpoint="web.get_cover",
                     view_func=lambda book_id, resolution: "")
    yield app.test_client(), searches

    session.close()
    engine.dispose()


def _get(client, url):
    response = client.get(url)
    return response.status_code, response.get_json()


def test_first_page_lists_newest_visible_books_and_flags_shelf_members(picker):
    client, _ = picker
    status, body = _get(client, "/api/v1/shelves/9/available-books")

    assert status == 200
    ids = [item["id"] for item in body["items"]]
    assert ids == list(range(35, 5, -1))          # newest first, one page of 30
    assert HIDDEN_ID not in ids
    assert body["total"] == 35 and body["has_more"] is True
    flags = {item["id"]: item["in_shelf"] for item in body["items"]}
    assert flags[35] is True and flags[34] is False
    assert body["items"][0]["cover_url"] == "/cover/35/sm"
    assert body["items"][1]["cover_url"] == "/user-cover/34"   # the reader's own cover


def test_last_page_returns_the_remainder_and_stops_paging(picker):
    client, _ = picker
    status, body = _get(client, "/api/v1/shelves/9/available-books?page=2")

    assert status == 200
    assert [item["id"] for item in body["items"]] == [5, 4, 3, 2, 1]
    assert body["page"] == 2 and body["has_more"] is False


def test_search_sorts_by_title_unwraps_rows_and_pages(picker):
    client, searches = picker
    status, body = _get(client, "/api/v1/shelves/9/available-books?query=book%203&page=1")

    assert status == 200
    assert searches == ["book 3"]
    # "Book 3", "Book 30".."Book 35", unwrapped from the search rows' `.Books`.
    assert [item["id"] for item in body["items"]] == [3, 30, 31, 32, 33, 34, 35]
    assert body["items"][-1]["in_shelf"] is True
    assert body["total"] == 7 and body["has_more"] is False

    status, body = _get(client, "/api/v1/shelves/9/available-books?query=book&page=2")
    # Title order: "Book 1", "Book 10".."Book 4" fill page 1; "Book 5".."Book 9" remain.
    assert [item["id"] for item in body["items"]] == [5, 6, 7, 8, 9]
    assert body["total"] == 35 and body["has_more"] is False


def test_search_applies_the_callers_visibility_filter(picker):
    """The add path refuses archived books, so search must not offer them."""
    client, _ = picker
    status, body = _get(client, "/api/v1/shelves/9/available-books?query=book%2099")

    assert status == 200
    assert body["items"] == [] and body["total"] == 0


def test_a_shelf_the_caller_cannot_edit_lists_nothing(picker):
    client, searches = picker
    status, body = _get(client, "/api/v1/shelves/10/available-books?query=book")

    assert status == 403
    assert "items" not in body
    assert searches == []


def test_unknown_shelf_is_not_found(picker):
    client, _ = picker
    status, _body = _get(client, "/api/v1/shelves/404/available-books")
    assert status == 404


def test_classic_modal_shares_the_query_and_its_permission_gate(picker):
    client, searches = picker
    status, body = _get(client, "/shelf/9/available_books?query=book%203")

    assert status == 200
    assert [book["id"] for book in body["books"]] == [3, 30, 31, 32, 33, 34, 35]
    assert body["books"][-1]["in_shelf"] is True
    assert body["books"][0]["cover"] == "/cover/3/sm"

    status, _body = _get(client, "/shelf/10/available_books")
    assert status == 403
    assert searches == ["book 3"]
