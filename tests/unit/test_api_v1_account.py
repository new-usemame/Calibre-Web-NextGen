# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for /api/v1 account — auth gating, password-change verification,
and profile validation. DB writes are mocked; the focus is the endpoint logic.
"""
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


def _user(**kw):
    defaults = dict(
        is_authenticated=True, is_anonymous=False, id=1,
        name="maggie", email="m@example.com", kindle_mail="",
        locale="en", default_language="all", password="HASH",
        kindle_mail_subject="", kobo_only_shelves_sync=0, opds_only_shelves_sync=0,
        ui_font_body="", ui_font_display="",
        role_admin=lambda: False, role_passwd=lambda: True,
        role_upload=lambda: False, role_edit=lambda: False,
        role_download=lambda: True, role_delete_books=lambda: False,
        role_edit_shelfs=lambda: True, role_viewer=lambda: True,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


# ── auth gating ──────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_account_anonymous_401():
    from cps.api import account as mod
    with _ctx("/api/v1/account", method="GET"):
        with patch.object(mod, "current_user",
                          SimpleNamespace(is_authenticated=False, is_anonymous=True)):
            resp = inspect.unwrap(mod.get_account)()
    assert resp[1] == 401


# ── password change ──────────────────────────────────────────────────────────

@pytest.mark.unit
def test_password_change_wrong_current_400():
    from cps.api import account as mod
    with _ctx("/api/v1/account/password", body={"current_password": "nope", "new_password": "Newpass123"}):
        with patch.object(mod, "current_user", _user()), \
             patch.object(mod, "check_password_hash", return_value=False):
            resp = inspect.unwrap(mod.change_password)()
    assert resp[1] == 400
    assert json.loads(resp[0].get_data())["error"]["code"] == "invalid_credentials"


@pytest.mark.unit
def test_password_change_policy_fail_400():
    from cps.api import account as mod
    with _ctx("/api/v1/account/password", body={"current_password": "ok", "new_password": "weak"}):
        with patch.object(mod, "current_user", _user()), \
             patch.object(mod, "check_password_hash", return_value=True), \
             patch.object(mod, "valid_password", side_effect=Exception("Password too weak")):
            resp = inspect.unwrap(mod.change_password)()
    assert resp[1] == 400
    assert "weak" in json.loads(resp[0].get_data())["error"]["message"].lower()


@pytest.mark.unit
def test_password_change_forbidden_when_no_passwd_role():
    from cps.api import account as mod
    user = _user(role_passwd=lambda: False, role_admin=lambda: False)
    with _ctx("/api/v1/account/password", body={"current_password": "ok", "new_password": "Newpass123"}):
        with patch.object(mod, "current_user", user):
            resp = inspect.unwrap(mod.change_password)()
    assert resp[1] == 403


@pytest.mark.unit
def test_password_change_success_204():
    from cps.api import account as mod
    user = _user()
    mock_session = MagicMock()
    with _ctx("/api/v1/account/password", body={"current_password": "ok", "new_password": "Newpass123"}):
        with patch.object(mod, "current_user", user), \
             patch.object(mod, "check_password_hash", return_value=True), \
             patch.object(mod, "valid_password", return_value="Newpass123"), \
             patch.object(mod, "generate_password_hash", return_value="NEWHASH"), \
             patch.object(mod, "ub", SimpleNamespace(session=mock_session)):
            resp = inspect.unwrap(mod.change_password)()
    assert resp[1] == 204
    assert user.password == "NEWHASH"
    assert mock_session.commit.called


# ── profile update ───────────────────────────────────────────────────────────

@pytest.mark.unit
def test_profile_update_invalid_email_400():
    from cps.api import account as mod
    user = _user()
    with _ctx("/api/v1/account/profile", body={"email": "bogus"}):
        with patch.object(mod, "current_user", user), \
             patch.object(mod, "valid_email", side_effect=Exception("Invalid Email address format")), \
             patch.object(mod, "ub", SimpleNamespace(session=MagicMock())):
            resp = inspect.unwrap(mod.update_profile)()
    assert resp[1] == 400


@pytest.mark.unit
def test_profile_update_locale_and_language():
    from cps.api import account as mod
    user = _user()
    mock_session = MagicMock()
    mock_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    with _ctx("/api/v1/account/profile", body={"locale": "de", "default_language": "eng"}):
        with patch.object(mod, "current_user", user), \
             patch.object(mod, "ub", SimpleNamespace(session=mock_session, UserAppPassword=MagicMock())), \
             patch.object(mod, "calibre_db", SimpleNamespace(speaking_language=lambda: [])), \
             patch.object(mod, "get_available_locale", return_value=[]), \
             patch.object(mod, "_", lambda s: s):  # flask_babel not initialized on the bare test app
            resp = inspect.unwrap(mod.update_profile)()
    # returns the serialized account (a Response, 200)
    assert user.locale == "de"
    assert user.default_language == "eng"
    assert mock_session.commit.called


# ── app passwords ─────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_create_app_password_anonymous_401():
    from cps.api import account as mod
    with _ctx("/api/v1/account/app-passwords", body={"label": "x"}):
        with patch.object(mod, "current_user", _user(is_anonymous=True)):
            resp = inspect.unwrap(mod.create_app_password)()
    assert resp[1] == 401


@pytest.mark.unit
def test_create_app_password_empty_label_400():
    from cps.api import account as mod
    with _ctx("/api/v1/account/app-passwords", body={"label": "   "}):
        with patch.object(mod, "current_user", _user()):
            resp = inspect.unwrap(mod.create_app_password)()
    assert resp[1] == 400


@pytest.mark.unit
def test_create_app_password_returns_token_once():
    from cps.api import account as mod
    mock_ub = MagicMock()
    created = {}

    class _Row:
        def __init__(self, **kw):
            self.id = 9
            self.created_at = None
            self.__dict__.update(kw)
            created.update(kw)
    mock_ub.UserAppPassword = _Row
    with _ctx("/api/v1/account/app-passwords", body={"label": "KOReader"}):
        with patch.object(mod, "current_user", _user(id=1)), \
             patch.object(mod, "ub", mock_ub), \
             patch.object(mod, "generate_password_hash", side_effect=lambda p: "H:" + p):
            resp = inspect.unwrap(mod.create_app_password)()
    body = json.loads(resp[0].get_data())
    assert resp[1] == 201
    assert body["label"] == "KOReader"
    assert len(body["token"]) > 20            # cleartext returned once
    assert created["password_hash"].startswith("H:")  # only the hash is stored
    mock_ub.session.add.assert_called_once()


@pytest.mark.unit
def test_revoke_app_password_not_found_404():
    from cps.api import account as mod
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = None
    with _ctx("/api/v1/account/app-passwords/5/delete"):
        with patch.object(mod, "current_user", _user(id=1)), patch.object(mod, "ub", mock_ub):
            resp = inspect.unwrap(mod.revoke_app_password)(5)
    assert resp[1] == 404


@pytest.mark.unit
def test_revoke_app_password_sets_revoked():
    from cps.api import account as mod
    row = SimpleNamespace(revoked=False)
    mock_ub = MagicMock()
    mock_ub.session.query.return_value.filter.return_value.first.return_value = row
    with _ctx("/api/v1/account/app-passwords/5/delete"):
        with patch.object(mod, "current_user", _user(id=1)), patch.object(mod, "ub", mock_ub):
            resp = inspect.unwrap(mod.revoke_app_password)(5)
    assert resp[1] == 204
    assert row.revoked is True


@pytest.mark.unit
def test_profile_update_accepts_new_fields():
    """Source-pin: update_profile handles the extended sync/subject/font fields."""
    src = inspect.getsource(__import__("cps.api.account", fromlist=["update_profile"]).update_profile)
    for field in ("kindle_mail_subject", "kobo_only_shelves_sync", "opds_only_shelves_sync",
                  "ui_font_body", "ui_font_display"):
        assert field in src


# ── #701 UI font presets ─────────────────────────────────────────────────────

def _profile_ctx(user, body):
    """Shared harness for update_profile happy/validation paths."""
    from cps.api import account as mod
    mock_session = MagicMock()
    return mod, _ctx("/api/v1/account/profile", body=body), patch.object(mod, "current_user", user), \
        patch.object(mod, "ub", SimpleNamespace(session=mock_session, UserAppPassword=MagicMock())), \
        patch.object(mod, "calibre_db", SimpleNamespace(speaking_language=lambda: [])), \
        patch.object(mod, "get_available_locale", return_value=[]), \
        patch.object(mod, "_", lambda s: s)


@pytest.mark.unit
def test_profile_update_valid_font_keys_persist():
    user = _user()
    mod, ctx, *patches = _profile_ctx(user, {"ui_font_body": "serif", "ui_font_display": "mono"})
    with ctx:
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            resp = inspect.unwrap(mod.update_profile)()
    assert user.ui_font_body == "serif"
    assert user.ui_font_display == "mono"


@pytest.mark.unit
def test_profile_update_rejects_unknown_body_font_400():
    """An arbitrary value (not a known preset key) must 400, not be stored —
    this is the guard that keeps an arbitrary string out of the CSS var."""
    user = _user()
    mod, ctx, *patches = _profile_ctx(user, {"ui_font_body": "Arial; } body { display:none }"})
    with ctx:
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            resp = inspect.unwrap(mod.update_profile)()
    assert resp[1] == 400
    assert user.ui_font_body == ""  # unchanged


@pytest.mark.unit
def test_profile_update_display_allowlist_is_stricter_than_body():
    """'serif' is a valid BODY preset but not a valid DISPLAY preset (the
    display default is already serif) — the display field must reject it."""
    user = _user()
    mod, ctx, *patches = _profile_ctx(user, {"ui_font_display": "serif"})
    with ctx:
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            resp = inspect.unwrap(mod.update_profile)()
    assert resp[1] == 400
    assert user.ui_font_display == ""


@pytest.mark.unit
def test_profile_update_empty_font_clears_to_default():
    """Empty string is valid (means 'theme default') and must persist."""
    user = _user(ui_font_body="serif")
    mod, ctx, *patches = _profile_ctx(user, {"ui_font_body": ""})
    with ctx:
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            resp = inspect.unwrap(mod.update_profile)()
    assert user.ui_font_body == ""


@pytest.mark.unit
@pytest.mark.parametrize("bad", [0, False, [], {}, 1])
def test_profile_update_rejects_non_string_font_400(bad):
    """A falsy/typed non-string (0, false, [], {}) must 400 — not be coerced
    to '' and silently reset the font to default (Greptile P2 on #713)."""
    user = _user(ui_font_body="serif")
    mod, ctx, *patches = _profile_ctx(user, {"ui_font_body": bad})
    with ctx:
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            resp = inspect.unwrap(mod.update_profile)()
    assert resp[1] == 400
    assert user.ui_font_body == "serif"  # unchanged, not reset to default


@pytest.mark.unit
def test_profile_update_null_font_means_default():
    """Explicit null is the intended 'reset to theme default' → persists as ''."""
    user = _user(ui_font_body="serif")
    mod, ctx, *patches = _profile_ctx(user, {"ui_font_body": None})
    with ctx:
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            resp = inspect.unwrap(mod.update_profile)()
    assert user.ui_font_body == ""


@pytest.mark.unit
def test_font_allowlists_match_frontend_ssot_keys():
    """The backend key allowlist must stay in lock-step with the SPA preset
    keys (frontend/src/lib/fonts.ts). If someone edits one, this catches the
    drift that would otherwise 400-reject a valid dropdown choice."""
    import re
    from pathlib import Path
    from cps.api.account import ALLOWED_UI_FONT_BODY, ALLOWED_UI_FONT_DISPLAY

    fonts_ts = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "fonts.ts").read_text()

    def _keys(const_name):
        block = fonts_ts.split(const_name, 1)[1].split("];", 1)[0]
        return set(re.findall(r"key:\s*'([^']*)'", block))

    assert _keys("UI_BODY_FONTS") == set(ALLOWED_UI_FONT_BODY)
    assert _keys("UI_DISPLAY_FONTS") == set(ALLOWED_UI_FONT_DISPLAY)
