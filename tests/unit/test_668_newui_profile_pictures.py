# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for #668 — the new UI honours the custom profile picture set
in the classic profile-pictures panel.

Symptom: a profile picture set in classic view was invisible in the SPA (top-bar
account control + /account both showed a placeholder glyph). Root cause: the SPA
never consumed the picture. Fix: /api/v1/auth/me now returns the *current* user's
avatar (only theirs, not every user's like the classic endpoint), built through a
single `_me_payload` used by /me, login, and magic-link so the field can't drift.
"""
import ast
import inspect
import json

import flask
import pytest
from unittest.mock import patch

import cps.api.auth as auth_mod


DATA_URI = "data:image/png;base64,iVBORw0KGgo="


def _app():
    from cps.api import api_v1
    app = flask.Flask(__name__)
    app.testing = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SECRET_KEY"] = "test"
    app.config["RATELIMIT_ENABLED"] = False
    app.register_blueprint(api_v1)
    return app


def _write(tmp_path, monkeypatch, obj_or_text):
    p = tmp_path / "user_profiles.json"
    p.write_text(obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text))
    monkeypatch.setattr(auth_mod, "_USER_PROFILES_JSON", str(p))
    return p


# ── _user_avatar ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_avatar_present_returns_data_uri(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"maggie": DATA_URI})
    assert auth_mod._user_avatar("maggie") == DATA_URI


@pytest.mark.unit
def test_avatar_absent_user_returns_none(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"someone_else": DATA_URI})
    assert auth_mod._user_avatar("maggie") is None


@pytest.mark.unit
def test_avatar_missing_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_mod, "_USER_PROFILES_JSON", str(tmp_path / "nope.json"))
    assert auth_mod._user_avatar("maggie") is None


@pytest.mark.unit
def test_avatar_malformed_json_returns_none(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "{ this is not json")
    assert auth_mod._user_avatar("maggie") is None


@pytest.mark.unit
def test_avatar_non_dict_json_returns_none(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, ["not", "a", "dict"])
    assert auth_mod._user_avatar("maggie") is None


@pytest.mark.unit
def test_avatar_non_image_value_is_rejected(tmp_path, monkeypatch):
    # A corrupted / hostile entry must not become an arbitrary URL the SPA renders.
    _write(tmp_path, monkeypatch, {"maggie": "javascript:alert(1)"})
    assert auth_mod._user_avatar("maggie") is None


@pytest.mark.unit
def test_avatar_non_string_value_returns_none(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"maggie": {"nested": "obj"}})
    assert auth_mod._user_avatar("maggie") is None


# ── /api/v1/auth/me carries the avatar ──────────────────────────────────────

@pytest.mark.unit
def test_me_payload_includes_avatar(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {"maggie": DATA_URI})
    from cps import ub, constants
    u = ub.User()
    u.id, u.name, u.locale, u.theme = 5, "maggie", "en", 1
    u.role = constants.ROLE_USER
    app = _app()
    with patch("cps.api.auth.current_user", u):
        resp = app.test_client().get("/api/v1/auth/me")
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["avatar"] == DATA_URI


@pytest.mark.unit
def test_me_payload_avatar_null_when_unset(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, {})
    from cps import ub, constants
    u = ub.User()
    u.id, u.name, u.locale, u.theme = 5, "maggie", "en", 1
    u.role = constants.ROLE_USER
    app = _app()
    with patch("cps.api.auth.current_user", u):
        resp = app.test_client().get("/api/v1/auth/me")
    body = resp.get_json()
    assert resp.status_code == 200
    assert "avatar" in body and body["avatar"] is None


# ── SSOT source-pin: the three me-shaped sites all go through _me_payload ────

@pytest.mark.unit
def test_me_shaped_routes_route_through_me_payload():
    """Pins that auth_me, auth_login, and the magic-link check all build their
    user payload via `_me_payload` — so the avatar (and features/instance_name)
    can never be dropped by a future re-inline of one site."""
    for fn_name in ("auth_me", "auth_login"):
        src = inspect.getsource(getattr(auth_mod, fn_name))
        assert "_me_payload(" in src, f"{fn_name} must build its payload via _me_payload"

    # The magic-link handler lives under whatever route name; scan the module for
    # any lingering hand-rolled `serialize_user(...)` + `["features"]` pair, which
    # is the pre-fix drift shape. Only `_me_payload` itself may call serialize_user.
    tree = ast.parse(inspect.getsource(auth_mod))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name not in ("_me_payload",):
            fsrc = ast.get_source_segment(inspect.getsource(auth_mod), node) or ""
            assert "serialize_user(" not in fsrc, (
                f"{node.name} calls serialize_user directly; route it through _me_payload"
            )


@pytest.mark.unit
def test_me_payload_is_single_builder():
    src = inspect.getsource(auth_mod._me_payload)
    assert 'payload["avatar"]' in src
    assert "_user_avatar(" in src
