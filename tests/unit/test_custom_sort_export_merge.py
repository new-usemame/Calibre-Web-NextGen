# SPDX-License-Identifier: GPL-3.0-or-later
"""Real query parity at the custom-sort/shared-catalog/export merge seam."""
import csv
import inspect
import io
from urllib.parse import urlencode

import pytest

from tests.unit.test_classic_custom_sort_query import (
    classic_library as shared_classic_library, sortable_library as shared_sortable_library,
)
from cps.api import books

classic_library = shared_classic_library
sortable_library = shared_sortable_library

pytestmark = pytest.mark.unit


@pytest.fixture
def export_library(classic_library, monkeypatch):
    from cps import db, ub
    state = classic_library
    state.user.is_authenticated = True
    state.user.role_browse_global = lambda: True
    with state.session.begin():
        state.session.execute(db.CustomColumns.__table__.insert(), {
            "id": 12, "name": "Difficulty", "datatype": "int", "is_multiple": False,
            "mark_for_delete": False})
        state.session.execute(db.Books.__table__.insert(), [
            {"id": 7, "title": "Book 7", "sort": "Book 7", "path": ".", "author_sort": ""},
            {"id": 8, "title": "Unrelated", "sort": "Unrelated", "path": ".", "author_sort": ""}])
        state.session.execute(db.Authors.__table__.insert(), [
            {"id": 9, "name": "Author A", "sort": "Author A"},
            {"id": 10, "name": "Author B", "sort": "Author B"}])
        state.session.execute(db.books_authors_link.insert(), [
            {"book": i, "author": 10 if i == 6 else 9} for i in range(1, 9)])
        state.session.add(ub.ReadBook(user_id=7, book_id=7, read_status=ub.ReadBook.STATUS_FINISHED))
    monkeypatch.setattr(ub, "session", state.session)
    monkeypatch.setattr(books, "calibre_db", state.library)
    monkeypatch.setattr(books, "config", state.library.config)
    monkeypatch.setattr(books, "current_user", state.user)
    monkeypatch.setattr(books, "_rows_to_items", lambda rows, *_args: [
        {"id": row.Books.id} for row in rows])
    return state


@pytest.mark.parametrize("direction,expected", [
    ("asc", [4,1,2,3,5]), ("desc", [2,1,4,5,3])])
@pytest.mark.parametrize("selection_filter", ["search", "favorites", "rated", "archived"])
def test_combined_visible_and_select_all_keep_custom_order(export_library, direction, expected, selection_filter):
    from cps import db, ub
    if selection_filter == "favorites":
        export_library.session.add_all([ub.FavoriteBook(user_id=7, book_id=i) for i in range(1, 7)])
    elif selection_filter == "archived":
        export_library.session.add_all([ub.ArchivedBook(user_id=7, book_id=i, is_archived=True) for i in range(1, 7)])
    elif selection_filter == "rated":
        export_library.session.execute(db.Ratings.__table__.insert(), {"id": 9, "rating": 10})
        export_library.session.execute(db.books_ratings_link.insert(), [
            {"book": i, "rating": 9} for i in range(1, 7)])
    export_library.session.commit()
    for select_all in [False, True]:
        params = {"author": 9, "filter": selection_filter,
            "sort": f"cc-12-{direction}", "select_all": int(select_all)}
        if selection_filter == "search":
            params.update(search="Book", filter="unread")
        args = urlencode(params)
        with export_library.app.test_request_context("/api/v1/books?" + args):
            result = inspect.unwrap(books.list_books)().get_json()
        actual = result["ids"] if select_all else [item["id"] for item in result["items"]]
        assert actual == expected
        assert result["total"] == 5
        if not select_all:
            assert result["sort"] == f"cc-12-{direction}"
            assert result["custom_column_definitions"][0]["id"] == 12


@pytest.mark.parametrize("direction", ["asc", "desc"])
@pytest.mark.parametrize("source", ["catalog", "advanced", "classic_advanced", "global"])
def test_compatible_export_keeps_actual_custom_order(export_library, direction, source):
    key = f"cc-12-{direction}"
    expected = [4,1,2,3,5] if direction == "asc" else [2,1,4,5,3]
    if source == "catalog":
        params = {"search": "Book", "author": 9, "filter": "unread", "sort": key}
    elif source == "advanced":
        params = {"title": "Book", "authors": "Author A", "read_status": "unread", "sort": key}
    elif source == "global":
        params = {"search": "Book", "sort": key}
        expected = [4,1,2,6,3,5,7] if direction == "asc" else [6,2,1,4,7,5,3]
    else:
        from cps.api.search import _json_to_term
        with export_library.app.test_request_context():
            token = books.create_classic_advanced_export_snapshot(_json_to_term({
                "title": "Book", "authors": "Author A", "read_status": "unread"}))
        params = {"snapshot": token, "sort": key}
    with export_library.app.test_request_context("/api/v1/books/export", method="POST",
            json={"source": source, "format": "csv", "params": params}):
        result = inspect.unwrap(books.export_book_list)()
        assert not isinstance(result, tuple), result
        try:
            rows = list(csv.reader(io.StringIO(result.get_data(as_text=True))))
            assert [row[0] for row in rows[1:]] == [f"Book {i}" for i in expected]
            assert result.headers["X-Export-Count"] == str(len(expected))
            assert result.headers["Cache-Control"] == "private, no-store"
        finally:
            result.close()


@pytest.mark.parametrize("key", ["cc-999-asc", "cc-12-sideways", "cc-12-asc;DROP TABLE books", "cc-١٢-asc", "hotdesc"])
def test_invalid_export_sort_refuses_without_modifying_books(export_library, key):
    from cps import db
    with pytest.raises(books.BookExportRequestError) as error:
        books._catalog_export_query({"sort": key})
    assert error.value.status == 400
    assert export_library.session.query(db.Books).count() == 8


def test_hidden_custom_sort_is_refused_and_outage_is_retryable(export_library, monkeypatch):
    export_library.library.config.config_columns_to_ignore = "Difficulty"
    with pytest.raises(books.BookExportRequestError) as hidden:
        books._catalog_export_query({"sort": "cc-12-asc"})
    assert hidden.value.status == 400
    export_library.library.config.config_columns_to_ignore = ""
    monkeypatch.setattr(books, "load_configured_columns", lambda _config: None)
    with pytest.raises(books.BookExportRequestError) as outage:
        books._catalog_export_query({"sort": "cc-12-asc"})
    assert outage.value.status == 503
