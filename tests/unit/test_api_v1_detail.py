# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for /api/v1 book detail, read-toggle, and serialize_book_detail."""
import json
import inspect
import datetime
import pytest
import flask
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from cps.db import Identifiers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_book(**kwargs):
    """Return a minimal SimpleNamespace Books-alike for tests."""
    defaults = dict(
        id=42,
        title="Dune",
        series_index="1.0",
        has_cover=1,
        authors=[SimpleNamespace(id=7, name="Frank Herbert")],
        series=[SimpleNamespace(id=3, name="Dune Chronicles")],
        data=[SimpleNamespace(format="EPUB", uncompressed_size=500_000, name="dune")],
        comments=[SimpleNamespace(text="<p>A spice epic.</p>")],
        tags=[SimpleNamespace(id=11, name="sci-fi")],
        languages=[SimpleNamespace(lang_code="eng", language_name="English")],
        publishers=[SimpleNamespace(id=5, name="Chilton Books")],
        identifiers=[Identifiers("9780441013593", "isbn", 1)],
        pubdate=datetime.datetime(1965, 8, 1),
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# serialize_book_detail — pure unit tests (no Flask app needed)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_serialize_book_detail_full():
    from cps.api.serializers import serialize_book_detail
    book = _make_book()
    out = serialize_book_detail(book, read=True, archived=False)

    assert out["id"] == 42
    assert out["title"] == "Dune"
    assert out["authors"] == [{"id": 7, "name": "Frank Herbert"}]
    assert out["series"] == {"id": 3, "name": "Dune Chronicles"}
    assert out["series_index"] == "1.0"
    assert out["cover_url"] == "/cover/42/og"
    assert out["pubdate"] == "1965-08-01"
    assert out["description_html"] == "<p>A spice epic.</p>"
    assert out["tags"] == [{"id": 11, "name": "sci-fi"}]
    assert out["languages"] == [{"id": "eng", "name": "English"}]
    assert out["publishers"] == [{"id": 5, "name": "Chilton Books"}]
    assert out["identifiers"] == [{
        "type": "isbn", "val": "9780441013593",
        "url": "https://www.worldcat.org/isbn/9780441013593", "label": "ISBN",
    }]
    assert len(out["formats"]) == 1
    fmt = out["formats"][0]
    assert fmt["format"] == "EPUB"
    assert fmt["size_bytes"] == 500_000
    assert fmt["download_url"] == "/download/42/epub/dune"
    assert fmt["read_url"] == "/read/42/epub"
    assert out["read"] is True
    assert out["archived"] is False


@pytest.mark.unit
def test_serialize_book_detail_empty():
    from cps.api.serializers import serialize_book_detail
    book = SimpleNamespace(
        id=1, title="Empty", series_index=None, has_cover=0,
        authors=[], series=[], data=[], comments=[], tags=[],
        languages=[], publishers=[], identifiers=[],
        pubdate=None,
    )
    out = serialize_book_detail(book, read=False, archived=False)

    assert out["cover_url"] is None
    assert out["pubdate"] is None
    assert out["description_html"] is None
    assert out["tags"] == []
    assert out["languages"] == []
    assert out["publishers"] == []
    assert out["identifiers"] == []
    assert out["formats"] == []
    assert out["read"] is False
    assert out["archived"] is False


@pytest.mark.unit
def test_serialize_book_detail_pubdate_sentinel_none():
    """Year <= 101 (Books.DEFAULT_PUBDATE = datetime(101,1,1)) must yield null pubdate."""
    from cps.api.serializers import serialize_book_detail
    book = _make_book(pubdate=datetime.datetime(101, 1, 1))
    out = serialize_book_detail(book)
    assert out["pubdate"] is None


@pytest.mark.unit
def test_serialize_book_detail_pubdate_year_zero_none():
    """Year 0 also yields null."""
    from cps.api.serializers import serialize_book_detail
    book = _make_book(pubdate=datetime.datetime(1, 1, 1))
    out = serialize_book_detail(book)
    assert out["pubdate"] is None


@pytest.mark.unit
def test_serialize_book_detail_language_fallback_to_lang_code():
    """If language_name attr is absent or None, fall back to lang_code."""
    from cps.api.serializers import serialize_book_detail
    lang = SimpleNamespace(lang_code="fra")  # no language_name attr
    book = _make_book(languages=[lang])
    out = serialize_book_detail(book)
    assert out["languages"] == [{"id": "fra", "name": "fra"}]


@pytest.mark.unit
def test_serialize_book_detail_no_series():
    from cps.api.serializers import serialize_book_detail
    book = _make_book(series=[])
    out = serialize_book_detail(book)
    assert out["series"] is None


@pytest.mark.unit
def test_serialize_pages_custom_column_and_preserve_false_zero():
    from cps.api.serializers import serialize_book_detail
    definitions = [
        SimpleNamespace(id=7, label="pages", name="Pages", datatype="int", is_multiple=False),
        SimpleNamespace(id=8, label="owned", name="Owned", datatype="bool", is_multiple=False),
    ]
    book = _make_book(
        custom_column_7=[SimpleNamespace(value=0, extra=None)],
        custom_column_8=[SimpleNamespace(value=False, extra=None)],
    )
    out = serialize_book_detail(book, custom_column_definitions=definitions)
    assert out["custom_columns"][0]["values"][0]["value"] == 0
    assert out["custom_columns"][1]["values"][0]["value"] is False


@pytest.mark.unit
def test_custom_columns_empty_none_and_hostile_comments():
    from cps.api.serializers import serialize_book_detail
    definitions = [
        SimpleNamespace(id=7, label="pages", name="Pages", datatype="int", is_multiple=False),
        SimpleNamespace(id=9, label="notes", name="Notes", datatype="comments", is_multiple=False),
    ]
    book = _make_book(
        custom_column_7=None,
        custom_column_9=[SimpleNamespace(value='<img src=x onerror="alert(1)"><b>Safe</b>', extra=None)],
    )
    out = serialize_book_detail(book, custom_column_definitions=definitions)
    assert [c["label"] for c in out["custom_columns"]] == ["notes"]
    value = out["custom_columns"][0]["values"][0]
    assert value["value"] is None
    assert "<img" not in value["value_html"]
    assert "&lt;img" in value["value_html"]
    assert "<b>Safe</b>" in value["value_html"]


# ---------------------------------------------------------------------------
# GET /api/v1/books/<id> — detail endpoint
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_detail_endpoint_found():
    from cps.api import books as books_mod
    from cps import ub

    STATUS_FINISHED = ub.ReadBook.STATUS_FINISHED
    fake_book = _make_book()

    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_request_context("/api/v1/books/42"):
        with patch.object(
            books_mod.calibre_db, "get_book_read_archived",
            return_value=(fake_book, STATUS_FINISHED, False),
        ), \
        patch.object(books_mod.config, "config_read_column", 0, create=True), \
        patch.object(books_mod.calibre_db, "get_cc_columns", return_value=[]), \
        patch.object(books_mod, "current_user", SimpleNamespace(is_authenticated=False, is_anonymous=True)), \
        patch("cps.api.books.get_locale", return_value="en"), \
        patch("cps.api.books.isoLanguages.get_language_name", return_value="English"):
            view = inspect.unwrap(books_mod.book_detail)
            resp = view(42)

    data = json.loads(resp.get_data(as_text=True))
    assert resp.status_code == 200
    assert data["id"] == 42
    assert data["read"] is True
    assert data["archived"] is False
    assert data["title"] == "Dune"


@pytest.mark.unit
def test_detail_endpoint_not_found():
    from cps.api import books as books_mod

    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_request_context("/api/v1/books/999"):
        with patch.object(
            books_mod.calibre_db, "get_book_read_archived",
            return_value=None,
        ), \
        patch.object(books_mod.config, "config_read_column", 0, create=True):
            view = inspect.unwrap(books_mod.book_detail)
            resp = view(999)

    assert resp[1] == 404
    data = json.loads(resp[0].get_data(as_text=True))
    assert data["error"]["code"] == "not_found"


@pytest.mark.unit
def test_detail_endpoint_archived_book():
    from cps.api import books as books_mod
    from cps import ub

    fake_book = _make_book()

    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_request_context("/api/v1/books/42"):
        with patch.object(
            books_mod.calibre_db, "get_book_read_archived",
            return_value=(fake_book, None, True),
        ), \
        patch.object(books_mod.config, "config_read_column", 0, create=True), \
        patch.object(books_mod.calibre_db, "get_cc_columns", return_value=[]), \
        patch.object(books_mod, "current_user", SimpleNamespace(is_authenticated=False, is_anonymous=True)), \
        patch("cps.api.books.get_locale", return_value="en"), \
        patch("cps.api.books.isoLanguages.get_language_name", return_value="English"):
            view = inspect.unwrap(books_mod.book_detail)
            resp = view(42)

    data = json.loads(resp.get_data(as_text=True))
    assert data["archived"] is True
    assert data["read"] is False


# ---------------------------------------------------------------------------
# POST /api/v1/books/<id>/read — read-toggle endpoint
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_read_toggle_true():
    from cps.api import books as books_mod

    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_request_context(
        "/api/v1/books/42/read",
        method="POST",
        json={"read": True},
        content_type="application/json",
    ):
        with patch.object(books_mod, "edit_book_read_status", return_value="") as mock_toggle:
            view = inspect.unwrap(books_mod.toggle_book_read)
            resp = view(42)

    mock_toggle.assert_called_once_with(42, True)
    data = json.loads(resp.get_data(as_text=True))
    assert resp.status_code == 200
    assert data["read"] is True


@pytest.mark.unit
def test_read_toggle_false():
    from cps.api import books as books_mod

    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_request_context(
        "/api/v1/books/42/read",
        method="POST",
        json={"read": False},
        content_type="application/json",
    ):
        with patch.object(books_mod, "edit_book_read_status", return_value="") as mock_toggle:
            view = inspect.unwrap(books_mod.toggle_book_read)
            resp = view(42)

    mock_toggle.assert_called_once_with(42, False)
    data = json.loads(resp.get_data(as_text=True))
    assert data["read"] is False


@pytest.mark.unit
def test_read_toggle_default_is_true():
    """Empty body → default read=True."""
    from cps.api import books as books_mod

    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False

    with app.test_request_context(
        "/api/v1/books/42/read",
        method="POST",
        content_type="application/json",
    ):
        with patch.object(books_mod, "edit_book_read_status", return_value="") as mock_toggle:
            view = inspect.unwrap(books_mod.toggle_book_read)
            resp = view(42)

    mock_toggle.assert_called_once_with(42, True)
    data = json.loads(resp.get_data(as_text=True))
    assert data["read"] is True
