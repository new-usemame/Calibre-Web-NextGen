# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fork #580 — the SPA edit page can now edit identifiers (ISBN/ASIN/…).

Backend: /api/v1/books/<id>/metadata accepts an `identifiers` list of {type,val},
reconciles it against the book's existing rows via the same modify_identifiers
helper the legacy editor uses, and returns the current identifiers in the editable
metadata so the form can seed the table.
"""
import inspect
import json
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import flask
import pytest

pytestmark = pytest.mark.unit


def _ctx(path, body=None):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    kwargs = {"method": "POST"}
    if body is not None:
        kwargs["json"] = body
        kwargs["content_type"] = "application/json"
    return app.test_request_context(path, **kwargs)


def _editor():
    return SimpleNamespace(is_authenticated=True, is_anonymous=False, name="ed",
                           role_edit=lambda: True, role_delete_books=lambda: True, id=1)


def _fake_book(identifiers=None):
    return SimpleNamespace(
        id=5, title="T", authors=[], series=[], series_index=1.0,
        tags=[], publishers=[], languages=[], comments=[], ratings=[],
        identifiers=identifiers if identifiers is not None else [],
    )


def test_editable_metadata_includes_identifiers():
    from cps.api import edit as mod
    book = _fake_book([SimpleNamespace(type="isbn", val="123"),
                       SimpleNamespace(type="amazon", val="B01")])
    out = mod._editable_metadata(book)
    assert out["identifiers"] == [
        {"type": "isbn", "val": "123"},
        {"type": "amazon", "val": "B01"},
    ]


def test_update_metadata_persists_identifiers_lowercased_and_skips_blank_rows():
    from cps.api import edit as mod
    session = MagicMock()
    captured = {}

    def fake_modify(inp, dbids, sess):
        captured["input"] = inp
        captured["dbids"] = dbids
        return True, False  # changed, no error

    body = {"identifiers": [
        {"type": "ISBN", "val": "9780000000001"},
        {"type": "", "val": "orphan-value"},   # blank type -> skipped
        {"type": "amazon", "val": ""},          # blank value -> skipped
        {"type": "Amazon", "val": "B01ABCDEFG"},
    ]}
    with _ctx("/api/v1/books/5/metadata", body=body):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db",
                          SimpleNamespace(get_book=lambda _id: _fake_book(), session=session)), \
             patch.object(mod, "modify_identifiers", side_effect=fake_modify), \
             patch.object(mod, "get_locale", return_value="en"):
            resp = inspect.unwrap(mod.update_metadata)(5)

    # only the two well-formed rows are built, types lowercased, book id threaded
    assert len(captured["input"]) == 2
    assert sorted(i.type for i in captured["input"]) == ["amazon", "isbn"]
    assert all(i.book == 5 for i in captured["input"])
    session.commit.assert_called_once()
    assert resp.status_code == 200


def test_update_metadata_duplicate_identifier_reports_field_error():
    from cps.api import edit as mod
    session = MagicMock()

    def fake_modify(inp, dbids, sess):
        # Realistic duplicate case: modify_identifiers may queue partial add/deletes
        # (changed=True) AND flag the duplicate (error=True). A rejected payload must
        # roll back, not commit the partial changes.
        return True, True

    body = {"identifiers": [{"type": "isbn", "val": "1"}, {"type": "isbn", "val": "2"}]}
    with _ctx("/api/v1/books/5/metadata", body=body):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db",
                          SimpleNamespace(get_book=lambda _id: _fake_book(), session=session)), \
             patch.object(mod, "modify_identifiers", side_effect=fake_modify), \
             patch.object(mod, "get_locale", return_value="en"):
            resp = inspect.unwrap(mod.update_metadata)(5)

    payload = json.loads(resp.get_data())
    assert "identifiers" in payload.get("errors", {})
    session.commit.assert_not_called()   # rejected payload must NOT persist
    session.rollback.assert_called()     # partial staged changes discarded


def test_update_metadata_without_identifiers_key_does_not_touch_them():
    """Omitting the key leaves identifiers alone (only present-in-payload fields
    are changed) — no modify_identifiers call, no commit."""
    from cps.api import edit as mod
    session = MagicMock()
    with _ctx("/api/v1/books/5/metadata", body={"title": "New"}):
        with patch.object(mod, "current_user", _editor()), \
             patch.object(mod, "calibre_db",
                          SimpleNamespace(get_book=lambda _id: _fake_book(), session=session)), \
             patch.object(mod, "modify_identifiers", side_effect=AssertionError("must not be called")), \
             patch.object(mod, "edit_book_param",
                          return_value=flask.Response(json.dumps({"success": True}),
                                                      mimetype="application/json")), \
             patch.object(mod, "get_locale", return_value="en"):
            resp = inspect.unwrap(mod.update_metadata)(5)
    assert resp.status_code == 200
    session.commit.assert_not_called()
