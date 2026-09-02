# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for POST /api/v1/books/<id>/convert validation."""
import json
import inspect
import pytest
import flask
from types import SimpleNamespace
from unittest.mock import patch


@pytest.mark.unit
def test_convert_rejects_invalid_target():
    """A bogus 'to' format (e.g. EXE) must be rejected with invalid_request.

    Regression: the endpoint previously accepted any dst string and only failed
    inside convert_book_format, letting nonsense targets queue work.
    """
    from cps.api import edit as edit_mod

    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False

    fake_book = SimpleNamespace(id=42)

    with app.test_request_context(
        "/api/v1/books/42/convert",
        method="POST",
        json={"from": "EPUB", "to": "EXE"},
        content_type="application/json",
    ):
        with patch.object(edit_mod, "current_user",
                          SimpleNamespace(is_authenticated=True, is_anonymous=False,
                                          role_edit=lambda: True, name="admin")), \
             patch.object(edit_mod.calibre_db, "get_filtered_book", return_value=fake_book), \
             patch.object(edit_mod, "get_convert_options",
                          return_value=(["epub"], ["mobi", "pdf"])):
            view = inspect.unwrap(edit_mod.convert_format)
            resp = view(42)

    assert resp[1] == 400
    data = json.loads(resp[0].get_data(as_text=True))
    assert data["error"]["code"] == "invalid_request"


@pytest.mark.unit
def test_convert_accepts_valid_source_and_target():
    """A src/dst pair inside the allowed lists queues the conversion."""
    from cps.api import edit as edit_mod

    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False

    fake_book = SimpleNamespace(id=42)

    with app.test_request_context(
        "/api/v1/books/42/convert",
        method="POST",
        json={"from": "EPUB", "to": "MOBI"},
        content_type="application/json",
    ):
        with patch.object(edit_mod, "current_user",
                          SimpleNamespace(is_authenticated=True, is_anonymous=False,
                                          role_edit=lambda: True, name="admin")), \
             patch.object(edit_mod.calibre_db, "get_filtered_book", return_value=fake_book), \
             patch.object(edit_mod, "get_convert_options",
                          return_value=(["epub"], ["mobi", "pdf"])), \
             patch.object(edit_mod.config, "get_book_path", return_value="/books"), \
             patch.object(edit_mod, "convert_book_format", return_value=None) as mock_convert:
            view = inspect.unwrap(edit_mod.convert_format)
            resp = view(42)

    assert resp.status_code == 200
    data = json.loads(resp.get_data(as_text=True))
    assert data["ok"] is True
    mock_convert.assert_called_once()


@pytest.mark.unit
def test_convert_rejects_invalid_source():
    """A src format not in the allowed sources must be rejected."""
    from cps.api import edit as edit_mod

    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False

    fake_book = SimpleNamespace(id=42)

    with app.test_request_context(
        "/api/v1/books/42/convert",
        method="POST",
        json={"from": "ACSM", "to": "MOBI"},
        content_type="application/json",
    ):
        with patch.object(edit_mod, "current_user",
                          SimpleNamespace(is_authenticated=True, is_anonymous=False,
                                          role_edit=lambda: True, name="admin")), \
             patch.object(edit_mod.calibre_db, "get_filtered_book", return_value=fake_book), \
             patch.object(edit_mod, "get_convert_options",
                          return_value=(["epub"], ["mobi", "pdf"])):
            view = inspect.unwrap(edit_mod.convert_format)
            resp = view(42)

    assert resp[1] == 400
    data = json.loads(resp[0].get_data(as_text=True))
    assert data["error"]["code"] == "invalid_request"
