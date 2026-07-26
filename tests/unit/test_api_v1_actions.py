# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for per-book action endpoints (cps/api/actions.py):
favorite / archived / hidden toggles + send-to-e-reader. Verifies anon gating,
the feature-flag gate on hide, and the send guards (mail config, download role,
recipient resolution)."""
import inspect
import json
import flask
import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock


def _ctx(path, method="POST", body=None):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    kwargs = {"method": method}
    if body is not None:
        kwargs["json"] = body
        kwargs["content_type"] = "application/json"
    return app.test_request_context(path, **kwargs)


def _user(anon=False, download=True, kindle="k@x.com"):
    return SimpleNamespace(
        is_authenticated=True, is_anonymous=anon, id=1,
        role_download=lambda: download, kindle_mail=kindle, kindle_mail_subject=None,
        name="alice",
    )


def _body(resp):
    return json.loads(resp[0].get_data() if isinstance(resp, tuple) else resp.get_data())


# ── favorite ──────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_favorite_anonymous_401():
    from cps.api import actions as mod
    with _ctx("/api/v1/books/5/favorite"):
        with patch.object(mod, "current_user", _user(anon=True)):
            resp = inspect.unwrap(mod.toggle_book_favorite)(5)
    assert resp[1] == 401


@pytest.mark.unit
def test_favorite_adds_when_absent():
    from cps.api import actions as mod
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = None
    with _ctx("/api/v1/books/5/favorite"):
        with patch.object(mod, "current_user", _user()), patch.object(mod, "ub", mock_ub):
            resp = inspect.unwrap(mod.toggle_book_favorite)(5)
    assert _body(resp)["favorited"] is True
    mock_ub.session.add.assert_called_once()


@pytest.mark.unit
def test_favorite_removes_when_present():
    from cps.api import actions as mod
    mock_ub = MagicMock()
    existing = SimpleNamespace()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = existing
    with _ctx("/api/v1/books/5/favorite"):
        with patch.object(mod, "current_user", _user()), patch.object(mod, "ub", mock_ub):
            resp = inspect.unwrap(mod.toggle_book_favorite)(5)
    assert _body(resp)["favorited"] is False
    mock_ub.session.delete.assert_called_once_with(existing)


# ── archived ──────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_archived_uses_core_and_resyncs():
    from cps.api import actions as mod
    with _ctx("/api/v1/books/5/archived"):
        with patch.object(mod, "current_user", _user()), \
             patch.object(mod, "change_archived_books", return_value=True) as core, \
             patch.object(mod, "remove_synced_book") as resync:
            resp = inspect.unwrap(mod.toggle_book_archived)(5)
    assert _body(resp)["archived"] is True
    core.assert_called_once()
    resync.assert_called_once_with(5)


# ── hidden ────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_hidden_unhide_always_allowed():
    from cps.api import actions as mod
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = SimpleNamespace()
    with _ctx("/api/v1/books/5/hidden"):
        with patch.object(mod, "current_user", _user()), patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "config", SimpleNamespace(config_user_hide_enabled=False)):
            resp = inspect.unwrap(mod.toggle_book_hidden)(5)
    assert _body(resp)["hidden"] is False


@pytest.mark.unit
def test_hidden_hide_blocked_when_feature_disabled():
    from cps.api import actions as mod
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = None
    with _ctx("/api/v1/books/5/hidden"):
        with patch.object(mod, "current_user", _user()), patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "config", SimpleNamespace(config_user_hide_enabled=False)):
            resp = inspect.unwrap(mod.toggle_book_hidden)(5)
    assert resp[1] == 403


@pytest.mark.unit
def test_hidden_hide_allowed_when_feature_enabled():
    from cps.api import actions as mod
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = None
    with _ctx("/api/v1/books/5/hidden"):
        with patch.object(mod, "current_user", _user()), patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "config", SimpleNamespace(config_user_hide_enabled=True)):
            resp = inspect.unwrap(mod.toggle_book_hidden)(5)
    assert _body(resp)["hidden"] is True
    mock_ub.session.add.assert_called_once()


@pytest.mark.unit
def test_hidden_desired_state_is_idempotent_across_stale_tabs():
    from cps.api import actions as mod
    mock_ub = MagicMock()
    existing = SimpleNamespace()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = existing
    with _ctx("/api/v1/books/5/hidden", body={"hidden": True}):
        with patch.object(mod, "current_user", _user()), patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "config", SimpleNamespace(config_user_hide_enabled=True)):
            resp = inspect.unwrap(mod.toggle_book_hidden)(5)
    assert _body(resp)["hidden"] is True
    mock_ub.session.delete.assert_not_called()


@pytest.mark.unit
def test_hidden_rejects_non_boolean_desired_state():
    from cps.api import actions as mod
    with _ctx("/api/v1/books/5/hidden", body={"hidden": "yes"}):
        with patch.object(mod, "current_user", _user()):
            resp = inspect.unwrap(mod.toggle_book_hidden)(5)
    assert resp[1] == 400
    assert _body(resp)["error"]["code"] == "invalid_request"


# ── send to e-reader ──────────────────────────────────────────────────────────

@pytest.mark.unit
def test_send_requires_download_role():
    from cps.api import actions as mod
    with _ctx("/api/v1/books/5/send", body={"format": "epub"}):
        with patch.object(mod, "current_user", _user(download=False)):
            resp = inspect.unwrap(mod.send_book_to_ereader)(5)
    assert resp[1] == 403


@pytest.mark.unit
def test_send_requires_mail_configured():
    from cps.api import actions as mod
    with _ctx("/api/v1/books/5/send", body={"format": "epub"}):
        with patch.object(mod, "current_user", _user()), \
             patch.object(mod, "config", SimpleNamespace(get_mail_server_configured=lambda: False)):
            resp = inspect.unwrap(mod.send_book_to_ereader)(5)
    assert resp[1] == 400
    assert _body(resp)["error"]["code"] == "mail_not_configured"


@pytest.mark.unit
def test_send_requires_format():
    from cps.api import actions as mod
    with _ctx("/api/v1/books/5/send", body={}):
        with patch.object(mod, "current_user", _user()), \
             patch.object(mod, "config", SimpleNamespace(get_mail_server_configured=lambda: True,
                                                         get_book_path=lambda: "/books")):
            resp = inspect.unwrap(mod.send_book_to_ereader)(5)
    assert resp[1] == 400
    assert _body(resp)["error"]["code"] == "invalid_request"


@pytest.mark.unit
def test_send_no_kindle_mail_no_emails_400():
    from cps.api import actions as mod
    with _ctx("/api/v1/books/5/send", body={"format": "epub"}):
        with patch.object(mod, "current_user", _user(kindle="")), \
             patch.object(mod, "config", SimpleNamespace(get_mail_server_configured=lambda: True,
                                                         get_book_path=lambda: "/books")):
            resp = inspect.unwrap(mod.send_book_to_ereader)(5)
    assert resp[1] == 400
    assert _body(resp)["error"]["code"] == "no_ereader_email"


@pytest.mark.unit
def test_send_success_calls_send_mail_and_records_download():
    from cps.api import actions as mod
    mock_ub = MagicMock()
    with _ctx("/api/v1/books/5/send", body={"format": "epub", "convert": True}):
        with patch.object(mod, "current_user", _user(kindle="k@x.com")), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "config", SimpleNamespace(get_mail_server_configured=lambda: True,
                                                         get_book_path=lambda: "/books")), \
             patch.object(mod, "send_mail", return_value=None) as sm:
            resp = inspect.unwrap(mod.send_book_to_ereader)(5)
    body = _body(resp)
    assert body["ok"] is True
    sm.assert_called_once()
    # convert flag passed through as 1
    assert sm.call_args.args[2] == 1
    mock_ub.update_download.assert_called_once()


@pytest.mark.unit
def test_send_failure_returns_502():
    from cps.api import actions as mod
    with _ctx("/api/v1/books/5/send", body={"format": "epub"}):
        with patch.object(mod, "current_user", _user(kindle="k@x.com")), \
             patch.object(mod, "config", SimpleNamespace(get_mail_server_configured=lambda: True,
                                                         get_book_path=lambda: "/books")), \
             patch.object(mod, "send_mail", return_value="SMTP exploded"):
            resp = inspect.unwrap(mod.send_book_to_ereader)(5)
    assert resp[1] == 502
