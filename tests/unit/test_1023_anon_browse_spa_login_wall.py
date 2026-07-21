# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#1023 / #1045: anonymous browsing is enabled but the new UI still shows a login wall.

Two independent reports landed within 40 minutes on 2026-07-20 -- a filed issue
("Users are being asked for credentials despite anonymous access being enabled")
and an anonymous in-app report from someone who *switched back to the classic
view* over it. That asymmetry is the whole diagnosis: the classic UI serves a
guest fine, the SPA does not.

Root cause, in two layers:

1. ``cps/api/__init__.py::_require_api_auth`` gates the whole ``/api/v1``
   surface and correctly honours ``config_anonbrowse``, so every data endpoint
   (``/books``, ``/browse/…``) already serves a guest. But ``auth_me`` sits in
   ``_PUBLIC_ENDPOINTS`` (it must, or login would be impossible) and then
   re-decides for itself with a bare ``current_user.is_authenticated`` check
   that never consults ``config_anonbrowse``. Flask-Login's anonymous user is
   ``ub.Anonymous`` -- a fully populated Guest row -- but ``is_authenticated``
   is False for it by definition, so ``/auth/me`` 401s. The SPA maps that 401
   to ``me = null`` (``lib/queries.ts::useMe``) and ``App.tsx`` renders the
   login tree for ``!me``. One endpoint's answer gates the entire app.

2. ``theme``, ``ui_font_body`` and ``ui_font_display`` are columns on ``User``,
   not on the shared ``UserBase`` mixin, and ``Anonymous.loadSettings`` copies
   the Guest row field-by-field by hand -- so it never picked them up. Any
   guest-reachable code path touching them raises ``AttributeError``. That is
   latent today only because layer 1 refuses the request first; fixing layer 1
   alone would have turned the 401 into a 500, and ``/api/v1/account`` reads
   the same three attributes.

Layer 2 is a drift bug, so the guard for it does not take a list on trust: it
AST-derives every attribute ``serialize_user`` reads off the user and requires
the real ``Anonymous`` to carry each one. A future column added to the
serializer but not to ``loadSettings`` fails here instead of in production.
"""

import ast
from pathlib import Path
from unittest.mock import patch

import flask
import pytest


pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SERIALIZERS = REPO_ROOT / "cps" / "api" / "serializers.py"


# --------------------------------------------------------------------------
# Layer 1 -- /auth/me must honour config_anonbrowse
# --------------------------------------------------------------------------

class _FakeGuest:
    """Stands in for ``ub.Anonymous``: a real Guest row whose ``is_authenticated``
    is False. Only the surface ``serialize_user`` touches is modelled; the real
    object's fidelity is pinned by the drift guard at the bottom of this file."""

    is_authenticated = False
    is_anonymous = True
    id = 2
    name = "Guest"
    locale = "en"
    theme = 1
    ui_font_body = ""
    ui_font_display = ""
    view_settings = {}
    kobo_only_shelves_sync = False
    sidebar_view = 0

    def role_admin(self): return False
    def role_upload(self): return False
    def role_edit(self): return False
    def role_download(self): return False
    def role_delete_books(self): return False
    def role_edit_shelfs(self): return False
    def role_viewer(self): return True
    def role_passwd(self): return False
    def role_anonymous(self): return True


def _app():
    from cps.api import api_v1
    app = flask.Flask(__name__)
    app.testing = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SECRET_KEY"] = "test"
    app.config["RATELIMIT_ENABLED"] = False
    app.register_blueprint(api_v1)
    return app


def _get_me(anonbrowse, authenticated=False):
    app = _app()
    with patch("cps.api.current_user") as gate_user, \
            patch("cps.api.config") as gate_cfg, \
            patch("cps.api.auth.current_user", _FakeGuest()), \
            patch("cps.api.auth.config") as cfg:
        gate_user.is_authenticated = authenticated
        gate_cfg.config_allow_reverse_proxy_header_login = False
        gate_cfg.config_anonbrowse = anonbrowse
        cfg.config_anonbrowse = anonbrowse
        cfg.config_calibre_web_title = "Test Library"
        cfg.config_books_per_page = 60
        cfg.config_random_books = 4
        cfg.get_mail_server_configured.return_value = False
        return app.test_client().get("/api/v1/auth/me")


def test_anon_browse_on_serves_guest_identity_not_401():
    """THE BUG. With anonymous browsing enabled, an unauthenticated /auth/me must
    return the Guest identity. Returning 401 makes the SPA render a login wall
    over a library every other endpoint would happily serve."""
    resp = _get_me(anonbrowse=1)
    assert resp.status_code == 200, (
        "anon-browse guest got %s from /auth/me -- the SPA renders its login "
        "wall for any non-200 here (#1023)" % resp.status_code)
    body = resp.get_json()
    assert body["name"] == "Guest"
    assert body["role"]["anonymous"] is True


def test_anon_browse_off_still_401s():
    """The fix must not open /me up when anonymous browsing is disabled --
    that would leak the Guest row's roles and sidebar config to the internet."""
    resp = _get_me(anonbrowse=0)
    assert resp.status_code == 401
    assert resp.get_json()["error"]["code"] == "unauthenticated"


def test_missing_anonbrowse_setting_fails_closed():
    """Bootstrap paths and several auth tests hand this module a minimal config
    object that has no ``config_anonbrowse`` attribute at all. A bare attribute
    access raises there and the blueprint's error handler converts it into a
    500, so an unconfigured instance would answer /me with a fault instead of a
    clean 401. The unknown case must read as 'not anonymous'."""
    app = _app()

    class _NoAnonSetting:
        """No config_anonbrowse -- mirrors the minimal bootstrap config."""

    with patch("cps.api.current_user") as gate_user, \
            patch("cps.api.config") as gate_cfg, \
            patch("cps.api.auth.current_user", _FakeGuest()), \
            patch("cps.api.auth.config", _NoAnonSetting()):
        gate_user.is_authenticated = False
        gate_cfg.config_allow_reverse_proxy_header_login = False
        gate_cfg.config_anonbrowse = 0
        resp = app.test_client().get("/api/v1/auth/me")

    assert resp.status_code == 401, (
        "an unconfigured instance must fail closed to 401, not fault to 500")
    assert resp.get_json()["error"]["code"] == "unauthenticated"


def test_anon_payload_is_flagged_anonymous_for_the_spa():
    """The SPA has to be able to tell 'guest' from 'signed in' -- it must offer
    a log-in affordance, not a log-out one, and hide account-only surfaces.
    role.anonymous is that signal and it rides the standard /me payload."""
    body = _get_me(anonbrowse=1).get_json()
    assert body["role"]["anonymous"] is True
    assert body["role"]["admin"] is False
    # The features block the SPA gates UI off must still be present for a guest.
    assert "features" in body and "anon_browse" in body["features"]


# --------------------------------------------------------------------------
# Layer 2 -- Anonymous must not drift from what serialize_user reads
# --------------------------------------------------------------------------

def _attributes_serialize_user_reads():
    """AST-derive every attribute ``serialize_user`` accesses on its ``user``
    argument, including the ``getattr(user, "x", default)`` form."""
    tree = ast.parse(SERIALIZERS.read_text())
    func = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "serialize_user")
    names = set()
    for node in ast.walk(func):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "user"):
            names.add(node.attr)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr" and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name) and node.args[0].id == "user"
                and isinstance(node.args[1], ast.Constant)):
            names.add(node.args[1].value)
    return names


def test_serialize_user_reads_a_known_set():
    """Guard the guard: if the AST walk silently stops matching, the drift test
    below would pass vacuously."""
    names = _attributes_serialize_user_reads()
    assert {"id", "name", "locale", "theme", "role_admin"} <= names
    assert len(names) >= 10


def test_anonymous_carries_every_field_serialize_user_reads(tmp_path):
    """THE SECOND BUG. ``Anonymous.loadSettings`` hand-copies the Guest row, so
    a column added to ``User`` and read by the serializer is simply missing on a
    guest -- ``theme``/``ui_font_body``/``ui_font_display`` are exactly that, and
    they make ``serialize_user(Anonymous())`` raise AttributeError."""
    from cps import ub

    ub.init_db(str(tmp_path / "app.db"))
    guest = ub.Anonymous()

    missing = sorted(n for n in _attributes_serialize_user_reads()
                     if not hasattr(guest, n))
    assert not missing, (
        "ub.Anonymous is missing %s -- serialize_user reads them off the user, "
        "so every guest-reachable endpoint using it raises AttributeError "
        "(#1023). Add them to Anonymous.loadSettings." % missing)


def test_serialize_user_actually_runs_on_a_real_anonymous(tmp_path):
    """End of the chain: the real object through the real serializer. This is
    the assertion that would have caught #1023 turning into a 500."""
    from cps import ub
    from cps.api.serializers import serialize_user

    ub.init_db(str(tmp_path / "app.db"))
    payload = serialize_user(ub.Anonymous())

    assert payload["name"] == "Guest"
    assert payload["role"]["anonymous"] is True
    assert "theme" in payload
