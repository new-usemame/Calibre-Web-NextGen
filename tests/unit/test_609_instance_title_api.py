# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#609: the new UI must honor the custom instance title (config_calibre_web_title).

The classic UI renders the configured title in the navbar and <title> on every
page (render_template.py passes instance=config.config_calibre_web_title). The
SPA hardcoded the brand, so a custom title never showed. These tests pin the
API contract that fixes it: instance_name on /auth/config (public — the login
screen needs it), /auth/me and the login payload (the app shell needs it).
"""
import re
from pathlib import Path
from unittest.mock import patch, MagicMock

import flask
import pytest

import cps.api.auth

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def _app():
    from cps.api import api_v1
    app = flask.Flask(__name__)
    app.testing = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SECRET_KEY"] = "test"
    app.config["RATELIMIT_ENABLED"] = False
    app.register_blueprint(api_v1)
    return app


@pytest.mark.unit
def test_auth_config_exposes_custom_instance_name():
    app = _app()
    with patch.object(cps.api.auth, "config") as cfg, \
         patch.object(cps.api.auth, "_oauth_providers", return_value=[]):
        cfg.config_calibre_web_title = "Glenn's Book Emporium"
        cfg.config_public_reg = False
        cfg.config_register_email = False
        cfg.get_mail_server_configured.return_value = False
        cfg.config_disable_standard_login = False
        cfg.config_remote_login = False
        resp = app.test_client().get("/api/v1/auth/config")
    assert resp.status_code == 200
    assert resp.get_json()["instance_name"] == "Glenn's Book Emporium"


@pytest.mark.unit
def test_auth_config_instance_name_falls_back_when_blank():
    app = _app()
    for blank in ("", None):
        with patch.object(cps.api.auth, "config") as cfg, \
             patch.object(cps.api.auth, "_oauth_providers", return_value=[]):
            cfg.config_calibre_web_title = blank
            cfg.config_public_reg = False
            cfg.config_register_email = False
            cfg.get_mail_server_configured.return_value = False
            cfg.config_disable_standard_login = False
            cfg.config_remote_login = False
            resp = app.test_client().get("/api/v1/auth/config")
        assert resp.get_json()["instance_name"] == "Calibre-Web NextGen"


@pytest.mark.unit
def test_auth_me_includes_instance_name():
    app = _app()
    from cps import ub, constants
    u = ub.User()
    u.id, u.name, u.locale, u.theme = 5, "maggie", "en", 1
    u.role = constants.ROLE_USER
    with patch("cps.api.auth.current_user", u), \
         patch.object(cps.api.auth, "config") as cfg:
        cfg.config_calibre_web_title = "Maggie's Library"
        cfg.config_user_hide_enabled = False
        cfg.get_mail_server_configured.return_value = False
        cfg.config_public_reg = False
        cfg.config_anonbrowse = False
        cfg.config_kobo_sync = False
        resp = app.test_client().get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.get_json()["instance_name"] == "Maggie's Library"


@pytest.mark.unit
def test_login_payload_includes_instance_name():
    app = _app()
    from cps import ub, constants
    u = ub.User()
    u.id, u.name, u.password, u.locale, u.theme = 1, "admin", "hash", "en", 1
    u.role = constants.ROLE_ADMIN
    mock_session = MagicMock()
    mock_session.query.return_value.filter.return_value.first.return_value = u
    with patch("cps.api.auth.ub.session", mock_session), \
         patch("cps.api.auth.check_password_hash", return_value=True), \
         patch.object(cps.api.auth, "config") as cfg, \
         patch("cps.api.auth.login_user"):
        cfg.config_disable_standard_login = False
        cfg.config_calibre_web_title = "Maggie's Library"
        cfg.config_user_hide_enabled = False
        cfg.get_mail_server_configured.return_value = False
        cfg.config_public_reg = False
        cfg.config_anonbrowse = False
        cfg.config_kobo_sync = False
        resp = app.test_client().post("/api/v1/auth/login",
                                      json={"username": "admin", "password": "x"})
    assert resp.status_code == 200
    assert resp.get_json()["instance_name"] == "Maggie's Library"


# --- Frontend source pins (no JS test runner in this repo; same idiom as the
# --- reload-delay pin in test_duplicate_manager_race_fix.py) ---------------

@pytest.mark.unit
def test_topbar_consumes_instance_name_not_only_hardcoded_brand():
    src = (FRONTEND / "components" / "TopBar.tsx").read_text()
    assert "instanceName" in src, "TopBar must take the server-provided instance name"


@pytest.mark.unit
def test_app_sets_document_title_from_instance_name():
    # Per-page title sync moved into the route-a11y hook (SC 2.4.2 Page Titled),
    # which App mounts as <RouteA11y>. Still classic <title> parity, just factored
    # out so titles also update on every SPA navigation, not only on login.
    route_a11y = (FRONTEND / "lib" / "a11y" / "useRouteA11y.ts").read_text()
    assert re.search(r"document\.title", route_a11y), \
        "the SPA must sync document.title (classic <title> parity)"
    app_src = (FRONTEND / "App.tsx").read_text()
    assert "RouteA11y" in app_src, "App must mount RouteA11y so titles sync on navigation"


@pytest.mark.unit
def test_api_types_declare_instance_name():
    src = (FRONTEND / "lib" / "api.ts").read_text()
    assert src.count("instance_name") >= 2, "Me and AuthConfig must both carry instance_name"
