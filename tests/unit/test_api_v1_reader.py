# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for /api/v1 reader bookmark endpoints (auth gate + format casing +
the save/clear write path). DB is mocked; legacy interop (same row, lowercase
format) is the key invariant pinned here."""
import inspect
import json
import flask
import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock


def _ctx(path, method="GET", body=None):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    kwargs = {"method": method}
    if body is not None:
        kwargs["json"] = body
        kwargs["content_type"] = "application/json"
    return app.test_request_context(path, **kwargs)


def _auth_user():
    return SimpleNamespace(is_authenticated=True, is_anonymous=False, id=1)


@pytest.mark.unit
def test_get_bookmark_anonymous_401():
    from cps.api import reader as mod
    with _ctx("/api/v1/books/5/bookmark?format=epub"):
        with patch.object(mod, "current_user",
                          SimpleNamespace(is_authenticated=False, is_anonymous=True)):
            resp = inspect.unwrap(mod.get_bookmark)(5)
    assert resp[1] == 401


@pytest.mark.unit
def test_get_bookmark_returns_key():
    from cps.api import reader as mod
    row = SimpleNamespace(bookmark_key="epubcfi(/6/4!/4/2)")
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = row
    with _ctx("/api/v1/books/5/bookmark?format=epub"):
        with patch.object(mod, "current_user", _auth_user()), patch.object(mod, "ub", mock_ub):
            resp = inspect.unwrap(mod.get_bookmark)(5)
    assert resp.status_code == 200
    assert json.loads(resp.get_data())["bookmark"] == "epubcfi(/6/4!/4/2)"


@pytest.mark.unit
def test_get_bookmark_none_when_absent():
    from cps.api import reader as mod
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = None
    with _ctx("/api/v1/books/5/bookmark"):
        with patch.object(mod, "current_user", _auth_user()), patch.object(mod, "ub", mock_ub):
            resp = inspect.unwrap(mod.get_bookmark)(5)
    assert json.loads(resp.get_data())["bookmark"] is None


@pytest.mark.unit
def test_save_bookmark_lowercases_format_and_merges():
    """Format must be stored lowercase (legacy interop) and the new bookmark merged."""
    from cps.api import reader as mod
    mock_ub = MagicMock()
    with _ctx("/api/v1/books/5/bookmark", method="POST",
              body={"format": "EPUB", "bookmark": "epubcfi(/6/8)"}):
        with patch.object(mod, "current_user", _auth_user()), patch.object(mod, "ub", mock_ub):
            resp = inspect.unwrap(mod.save_bookmark)(5)
    assert resp[1] == 204
    _args, kwargs = mock_ub.Bookmark.call_args
    assert kwargs["format"] == "epub", "format must be lowercased for legacy interop"
    assert kwargs["bookmark_key"] == "epubcfi(/6/8)"
    assert mock_ub.session.merge.called


@pytest.mark.unit
def test_save_empty_bookmark_clears_without_merge():
    from cps.api import reader as mod
    mock_ub = MagicMock()
    with _ctx("/api/v1/books/5/bookmark", method="POST", body={"format": "epub", "bookmark": ""}):
        with patch.object(mod, "current_user", _auth_user()), patch.object(mod, "ub", mock_ub):
            resp = inspect.unwrap(mod.save_bookmark)(5)
    assert resp[1] == 204
    assert mock_ub.session.query.return_value.filter.return_value.delete.called
    assert not mock_ub.session.merge.called


@pytest.mark.unit
def test_get_reader_settings_returns_complete_defaults_plus_saved_values():
    from cps.api import reader as mod
    user = _auth_user()
    user.view_settings = {"reader": {"font": "Arial", "margin": 32}}
    with _ctx("/api/v1/reader/settings"):
        with patch.object(mod, "current_user", user):
            resp = inspect.unwrap(mod.get_reader_settings)()
    body = json.loads(resp.get_data())["reader"]
    assert body["font"] == "Arial"
    assert body["margin"] == 32
    assert body["lineHeight"] == 150
    assert body["theme"] == "lightTheme"


@pytest.mark.unit
def test_save_reader_settings_merges_partial_patch_without_erasing_siblings():
    from cps.api import reader as mod
    user = _auth_user()
    user.view_settings = {"reader": {"font": "Arial", "margin": 32, "fontSize": 120}}
    mock_ub = MagicMock()
    with _ctx("/api/v1/reader/settings", method="POST", body={"lineHeight": 180}):
        with patch.object(mod, "current_user", user), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "flag_modified"):
            resp = inspect.unwrap(mod.save_reader_settings)()
    assert resp.status_code == 200
    assert user.view_settings["reader"] == {
        "font": "Arial", "margin": 32, "fontSize": 120, "lineHeight": 180,
    }
    assert json.loads(resp.get_data())["reader"]["lineHeight"] == 180
    mock_ub.session.commit.assert_called_once()


@pytest.mark.unit
def test_save_reader_settings_rejects_non_object_payload():
    from cps.api import reader as mod
    user = _auth_user()
    user.view_settings = {}
    with _ctx("/api/v1/reader/settings", method="POST", body=["bad"]):
        with patch.object(mod, "current_user", user):
            resp = inspect.unwrap(mod.save_reader_settings)()
    assert resp[1] == 400
