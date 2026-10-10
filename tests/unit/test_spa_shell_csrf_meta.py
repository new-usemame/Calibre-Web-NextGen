# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Clients that scrape the login page for a CSRF token can still sign in.

Calibre Web Companion 2.3.1 (the current F-Droid build) signs in the way the
Classic UI always allowed: GET ``/login`` with ``Accept: text/html`` and no
Fetch Metadata, read the token from the page, POST the form with it. NextGen
now sends that request to the SPA shell, which carried no token, so the app
stopped at "CSRF token not found". The shell exposes the session's token as
``<meta name="csrf-token">``; this replays the app's flow against the real
spa blueprint, the real routing predicate and real Flask-WTF enforcement.
"""
from html.parser import HTMLParser

import flask
import pytest
from flask_wtf.csrf import CSRFProtect

import cps.spa as spa_mod

# Companion 2.3.1's GET headers for the token fetch (api_service.dart).
_COMPANION_GET = {"Accept": "text/html,application/xhtml+xml,application/xml"}


class _CompanionTokenScraper(HTMLParser):
    """The selectors Companion 2.3.1 tries, in order, in _extractCsrfFromHtml."""

    def __init__(self):
        super().__init__()
        self.found = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        name = a.get("name")
        if tag in ("input", "meta") and name in ("csrf_token", "csrf-token", "_csrf"):
            value = (a.get("content") or a.get("value") or "").strip()
            if value:
                self.found.setdefault((tag, name), value)


def _scrape(html):
    p = _CompanionTokenScraper()
    p.feed(html)
    return next(iter(p.found.values()), None)


def _app(monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><head><title>Calibre-Web NextGen</title></head>"
        "<body><div id=root></div></body></html>")
    monkeypatch.setattr(spa_mod, "_SPA_DIR", str(tmp_path))
    monkeypatch.setenv("CWNG_SPA", "1")
    app = flask.Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["WTF_CSRF_SSL_STRICT"] = False
    CSRFProtect(app)
    app.register_blueprint(spa_mod.spa)

    # Mirrors web.login's routing decision with the real predicate.
    @app.route("/login", methods=["GET"])
    def login():
        if spa_mod.preferred_spa_html_request():
            return flask.redirect(spa_mod.spa_shell_url())
        return "classic"

    @app.route("/login", methods=["POST"])
    def login_post():
        flask.session["user"] = flask.request.form["username"]
        return flask.redirect("/")

    return app


@pytest.mark.unit
def test_companion_231_login_finds_token_and_signs_in(monkeypatch, tmp_path):
    client = _app(monkeypatch, tmp_path).test_client()

    page = client.get("/login", headers=_COMPANION_GET, follow_redirects=True)
    assert page.request.path == "/app/"  # the request really lands on the shell
    token = _scrape(page.get_data(as_text=True))
    assert token, "no CSRF token in the page a scraping client receives"

    # Enforcement is real: the same POST without the token is refused.
    refused = client.post("/login", data={"username": "reader", "password": "x"})
    assert refused.status_code == 400

    resp = client.post(
        "/login",
        data={"username": "reader", "password": "x", "csrf_token": token},
        headers={"X-CSRFToken": token, "X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert sess.get("user") == "reader"


@pytest.mark.unit
def test_shell_without_csrf_enforcement_has_no_token(monkeypatch, tmp_path):
    """No Flask-WTF on the app -> no meta tag, rather than a 500."""
    (tmp_path / "index.html").write_text("<html><head></head><body></body></html>")
    monkeypatch.setattr(spa_mod, "_SPA_DIR", str(tmp_path))
    monkeypatch.setenv("CWNG_SPA", "1")
    app = flask.Flask(__name__)
    app.register_blueprint(spa_mod.spa)
    resp = app.test_client().get("/app/")
    assert resp.status_code == 200
    assert b'name="csrf-token"' not in resp.data
