"""Regression tests for #587 — the new-UI book page didn't show the KOReader/Kobo
synced reading progress (the classic page shows "KOReader Progress: X%"). The
detail endpoint now surfaces it and the SPA book page renders it.

The endpoint reads the same source as the classic view
(KoboReadingState.current_bookmark.progress_percent); these pins fail on main
(which has neither the query nor the field) and the behaviour is additionally
verified live on the wire. A behavioural endpoint test with a real progress row
follows.
"""
import inspect
import pathlib
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import flask
import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_detail_endpoint_surfaces_kosync_progress():
    """An authenticated user with a synced KoboReadingState gets kosync_progress
    in the detail payload; the favorite/hidden lookups still read as absent."""
    from cps.api import books as books_mod
    from cps import ub

    fake_book = SimpleNamespace(
        id=7, title="The Time Machine", series_index="1.0", has_cover=1,
        authors=[], series=[], data=[], comments=[], tags=[],
        languages=[], publishers=[], identifiers=[], pubdate=None,
    )

    def query_side_effect(model):
        q = MagicMock()
        if model is ub.KoboReadingState:
            q.filter.return_value.first.return_value = SimpleNamespace(
                current_bookmark=SimpleNamespace(progress_percent=45.0))
        else:  # FavoriteBook / UserHiddenBook — not present
            q.filter.return_value.first.return_value = None
        return q

    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books/7"):
        with patch.object(books_mod.calibre_db, "get_book_read_archived",
                          return_value=(fake_book, 0, False)), \
             patch.object(books_mod.config, "config_read_column", 0, create=True), \
             patch.object(books_mod, "current_user",
                          SimpleNamespace(is_authenticated=True, is_anonymous=False, id=1)), \
             patch.object(books_mod.ub, "session", MagicMock(query=MagicMock(side_effect=query_side_effect))), \
             patch("cps.api.books.get_locale", return_value="en"), \
             patch("cps.api.books.isoLanguages.get_language_name", return_value="English"):
            import json
            resp = inspect.unwrap(books_mod.book_detail)(7)
    data = json.loads(resp.get_data(as_text=True))
    assert data["kosync_progress"] == 45.0


@pytest.mark.unit
def test_detail_endpoint_null_progress_when_unsynced():
    from cps.api import books as books_mod
    from cps import ub

    fake_book = SimpleNamespace(
        id=7, title="T", series_index="1.0", has_cover=0,
        authors=[], series=[], data=[], comments=[], tags=[],
        languages=[], publishers=[], identifiers=[], pubdate=None,
    )
    app = flask.Flask(__name__)
    with app.test_request_context("/api/v1/books/7"):
        with patch.object(books_mod.calibre_db, "get_book_read_archived",
                          return_value=(fake_book, 0, False)), \
             patch.object(books_mod.config, "config_read_column", 0, create=True), \
             patch.object(books_mod, "current_user",
                          SimpleNamespace(is_authenticated=True, is_anonymous=False, id=1)), \
             patch.object(books_mod.ub, "session",
                          MagicMock(query=MagicMock(return_value=MagicMock(
                              filter=MagicMock(return_value=MagicMock(
                                  first=MagicMock(return_value=None))))))), \
             patch("cps.api.books.get_locale", return_value="en"), \
             patch("cps.api.books.isoLanguages.get_language_name", return_value="English"):
            import json
            resp = inspect.unwrap(books_mod.book_detail)(7)
    data = json.loads(resp.get_data(as_text=True))
    assert data["kosync_progress"] is None


@pytest.mark.unit
def test_bookdetail_renders_progress():
    src = (_ROOT / "frontend" / "src" / "pages" / "BookDetail.tsx").read_text()
    assert "book.kosync_progress != null" in src
    assert "KOReader progress" in src
