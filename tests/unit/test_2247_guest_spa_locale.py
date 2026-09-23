# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2247: the New UI is always English for the Guest account.

The classic pages resolve the Guest's language from ``Accept-Language``:
``cw_babel.get_locale`` deliberately skips the Guest row's stored locale,
which is ``'en'`` by default. The SPA never asks Babel. It takes its language
from ``/api/v1/auth/me``'s ``locale``, and ``serialize_user`` reported the raw
row, so a German browser got ``'en'``, the SPA skipped loading a catalog, and
every guest page rendered in English while the classic view rendered German.

These drive the real ``/auth/me`` route and ``serialize_user`` with a shipped
translation set, so they discriminate on the value the SPA actually receives.
"""

from unittest.mock import patch

import flask
import pytest

from cps import cw_babel


pytestmark = pytest.mark.unit

SHIPPED = {"en", "de", "fr"}


class _Account:
    """The surface ``serialize_user`` reads. ``name == 'Guest'`` is what makes
    ``get_locale`` treat an account as the anonymous-browse guest."""

    id = 2
    theme = 1
    ui_font_body = ""
    ui_font_display = ""
    view_settings = {}
    kobo_only_shelves_sync = False
    sidebar_view = 0

    def __init__(self, name, locale, authenticated):
        self.name = name
        self.locale = locale
        self.is_authenticated = authenticated
        self.is_anonymous = not authenticated

    def role_admin(self): return False
    def role_upload(self): return False
    def role_edit(self): return False
    def role_download(self): return False
    def role_delete_books(self): return False
    def role_edit_shelfs(self): return False
    def role_viewer(self): return True
    def role_passwd(self): return False
    def role_anonymous(self): return self.is_anonymous


def _guest():
    return _Account("Guest", "en", authenticated=False)


def _me_locale(account, accept_language, monkeypatch):
    from cps.api import api_v1
    app = flask.Flask(__name__)
    app.testing = True
    app.config["SECRET_KEY"] = "test"
    app.config["RATELIMIT_ENABLED"] = False
    app.register_blueprint(api_v1)
    monkeypatch.setattr(cw_babel, "get_available_translations", lambda: SHIPPED)
    monkeypatch.setattr(cw_babel, "current_user", account)
    headers = {"Accept-Language": accept_language} if accept_language else {}
    with patch("cps.api.current_user") as gate_user, \
            patch("cps.api.config") as gate_cfg, \
            patch("cps.api.auth.current_user", account), \
            patch("cps.api.auth.config") as cfg:
        gate_user.is_authenticated = account.is_authenticated
        gate_cfg.config_allow_reverse_proxy_header_login = False
        gate_cfg.config_anonbrowse = 1
        cfg.config_anonbrowse = 1
        cfg.config_calibre_web_title = "Test Library"
        cfg.config_books_per_page = 60
        cfg.config_random_books = 4
        cfg.get_mail_server_configured.return_value = False
        resp = app.test_client().get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["locale"]


def test_guest_me_follows_the_browser_language(monkeypatch):
    """THE BUG. A German browser browsing as Guest must get 'de' from /me --
    the same language the classic page renders for that request -- not the
    Guest row's stored 'en'."""
    assert _me_locale(_guest(), "de-DE,de;q=0.9,en;q=0.5", monkeypatch) == "de"


def test_guest_without_a_usable_browser_language_stays_english(monkeypatch):
    """No preference, or only languages we do not ship, still means English."""
    assert _me_locale(_guest(), None, monkeypatch) == "en"
    assert _me_locale(_guest(), "xx-YY", monkeypatch) == "en"


def test_a_signed_in_users_saved_language_still_wins(monkeypatch):
    """Only the Guest follows the browser. A signed-in account that chose
    French keeps French in a German browser, exactly as the classic UI does."""
    alice = _Account("alice", "fr", authenticated=True)
    assert _me_locale(alice, "de", monkeypatch) == "fr"


def test_serialize_user_reports_the_account_it_describes(monkeypatch):
    """Login and magic-link build /me for the account that just signed in,
    within a request whose current user may still be the Guest. The locale
    must come from the described account, not from whoever the request's
    current user happens to be."""
    from cps.api.serializers import serialize_user

    app = flask.Flask(__name__)
    monkeypatch.setattr(cw_babel, "get_available_translations", lambda: SHIPPED)
    monkeypatch.setattr(cw_babel, "current_user", _guest())
    alice = _Account("alice", "fr", authenticated=True)
    with app.test_request_context("/", headers={"Accept-Language": "de"}):
        assert serialize_user(alice)["locale"] == "fr"
