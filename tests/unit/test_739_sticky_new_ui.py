# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#739: choosing the new UI is not sticky — nothing persists the choice, so a
later visit to a classic page (or opening one in a new tab) reverts to classic
and the "Try the new UI" nudge shows again.

Server-side fix (this file): the SPA shell sets a cwng_prefer_spa cookie when it
loads; the classic web index ('/') redirects to the shell while the cookie is
present, and the SPA's "Back to classic view" nav (the cwng_feedback marker)
clears it. layout.html hides the nudge banner and relabels the header pill once
the cookie is set.

The web index (cps/web.py) can't be driven directly in a unit test — it sits
behind login and pulls the Calibre DB via render_books_list — so tests b/c/d
exercise the REAL spa.py helpers through a minimal Flask app whose '/' route
wires them exactly like web.py:index does (that wiring equivalence is pinned by
the last test). The SPA-shell cookie (test a) is hit over HTTP on the real spa
blueprint; the template gating (test e) is a source-pin.

Cookie mechanics: the test client is created with use_cookies=False so its own
(empty) cookie jar doesn't clobber the HTTP_COOKIE we inject per-request, and we
read Set-Cookie straight off resp.headers — so nothing depends on the
set_cookie() test-client API, which changed signature across the supported Flask
range (1.x–3.x).
"""
import pathlib
from unittest.mock import MagicMock, patch

import flask
import pytest

import cps.spa as spa_mod

_REPO = pathlib.Path(__file__).resolve().parents[2]
_LAYOUT = _REPO / "cps" / "templates" / "layout.html"
_WEB = _REPO / "cps" / "web.py"

_HTML_ACCEPT = {"Accept": "text/html,application/xhtml+xml"}
_PREFER_COOKIE = {"HTTP_COOKIE": "cwng_prefer_spa=1"}


def _seed_bundle(tmp_path):
    """A minimal built index.html so the shell serves 200 (the Fast CI job never
    runs the Vite build). Mirrors the test_spa_shell.py / test_571 fixture."""
    (tmp_path / "index.html").write_text(
        "<!doctype html><title>Calibre-Web NextGen</title><div id=root></div>")
    monkey = pytest.MonkeyPatch()
    monkey.setattr(spa_mod, "_SPA_DIR", str(tmp_path))
    monkey.setenv("CWNG_SPA", "1")
    return monkey


def _mirror_prod_session_config(app):
    """A bare flask.Flask() leaves SESSION_COOKIE_SAMESITE=None (Flask default),
    so the preference cookie — which mirrors the session cookie's SameSite —
    would omit it. cps/__init__.py forces 'Lax' (and Secure under OAuth/HTTPS);
    replicate the standard-login shape so the SameSite assertion is meaningful."""
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config.setdefault("SESSION_COOKIE_SECURE", False)


def _spa_only_app(tmp_path):
    """App with just the spa blueprint — for the /app cookie-set test (a)."""
    monkey = _seed_bundle(tmp_path)
    app = flask.Flask(__name__)
    _mirror_prod_session_config(app)
    app.register_blueprint(spa_mod.spa)
    return app, monkey


def _sticky_app(tmp_path):
    """App with the spa blueprint + a '/' route that mirrors web.py:index's
    sticky-UI wiring: cwng_feedback clears the cookie, otherwise redirect when
    the helper says so. The helpers are the real production code; the only
    stand-in is render_books_list (→ a placeholder string) and the auth stack."""
    monkey = _seed_bundle(tmp_path)
    app = flask.Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    _mirror_prod_session_config(app)
    app.register_blueprint(spa_mod.spa)

    @app.route("/")
    def _classic_index_stand_in():
        if flask.request.args.get("cwng_feedback"):
            resp = flask.make_response("CLASSIC HOME")
            spa_mod.clear_prefer_spa_cookie(resp)
            return resp
        if spa_mod.classic_index_redirects_to_spa():
            return flask.redirect(spa_mod.spa_shell_url())
        return "CLASSIC HOME"

    return app, monkey


def _login_app(tmp_path):
    """App with the real SPA and web blueprints for anonymous /login routing.

    The classic login renderer is patched at its final template boundary so the
    tests exercise the production ``web.login`` route and all routing decisions
    without needing a configured user database, OAuth provider, or Jinja tree.
    """
    import cps.web as web_mod

    monkey = _seed_bundle(tmp_path)
    app = flask.Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    _mirror_prod_session_config(app)
    app.register_blueprint(spa_mod.spa)
    app.register_blueprint(web_mod.web)
    return app, web_mod, monkey


def _get_login(client, web_mod, *args, **kwargs):
    anonymous = MagicMock()
    anonymous.is_authenticated = False
    with patch.object(web_mod, "current_user", anonymous), \
         patch.object(web_mod, "render_login", return_value="CLASSIC LOGIN"), \
         patch.object(web_mod.config, "config_login_type", 0, create=True):
        return client.get(*args, **kwargs)


def _client(app):
    """use_cookies=False: we inject cookies per-request via environ_overrides and
    read Set-Cookie off resp.headers, sidestepping the version-volatile
    test-client cookie API."""
    return app.test_client(use_cookies=False)


def _set_cookie(resp):
    return ", ".join(resp.headers.getlist("Set-Cookie"))


@pytest.mark.unit
def test_a_app_shell_sets_prefer_cookie(tmp_path):
    """(a) GET /app stamps cwng_prefer_spa=1 — loading the new UI is the act of
    choosing it. On main (no persistence) no such cookie is set."""
    app, monkey = _spa_only_app(tmp_path)
    try:
        resp = _client(app).get("/app", headers=_HTML_ACCEPT)
        assert resp.status_code == 200
        sc = _set_cookie(resp)
        assert "cwng_prefer_spa=1" in sc
        assert "Path=/" in sc
        assert "SameSite=Lax" in sc
        assert "Max-Age=31536000" in sc  # one year (60*60*24*365)
        assert "HttpOnly" not in sc      # httponly=False — SPA runtime may read it
    finally:
        monkey.undo()


@pytest.mark.unit
def test_app_shell_cookie_path_under_subpath(tmp_path):
    """Behind a reverse-proxy subpath (script_root=/cwa) the cookie path must be
    the app root (/cwa), not '/' — so two CWNG instances on different subpaths of
    one host don't share the preference, and the path matches between set and
    delete. Mirrors how Flask scopes the session cookie (#571 precedent)."""
    app, monkey = _spa_only_app(tmp_path)
    try:
        resp = _client(app).get(
            "/app", headers=_HTML_ACCEPT,
            environ_overrides={"SCRIPT_NAME": "/cwa"})
        assert resp.status_code == 200
        sc = _set_cookie(resp)
        assert "Path=/cwa" in sc
        assert "Path=/" not in sc.replace("Path=/cwa", "")  # not the bare root
    finally:
        monkey.undo()


@pytest.mark.unit
def test_b_classic_index_redirects_when_cookie_present(tmp_path):
    """(b) GET / with the cookie + SPA enabled → 302 to /app. On main this is a
    200 classic home (the regression)."""
    app, monkey = _sticky_app(tmp_path)
    try:
        resp = _client(app).get(
            "/", headers=_HTML_ACCEPT, environ_overrides=_PREFER_COOKIE)
        assert resp.status_code == 302
        assert resp.headers["Location"].rstrip("/").endswith("/app")
    finally:
        monkey.undo()


@pytest.mark.unit
def test_c_feedback_clears_cookie_and_does_not_redirect(tmp_path):
    """(c) GET /?cwng_feedback=newui → 200 (NOT a redirect) and the response
    deletes cwng_prefer_spa. This is the SPA's 'Back to classic' nav path."""
    app, monkey = _sticky_app(tmp_path)
    try:
        resp = _client(app).get(
            "/?cwng_feedback=newui",
            headers=_HTML_ACCEPT, environ_overrides=_PREFER_COOKIE)
        assert resp.status_code == 200
        sc = _set_cookie(resp)
        assert "cwng_prefer_spa=" in sc
        assert "Max-Age=0" in sc  # deletion
    finally:
        monkey.undo()


@pytest.mark.unit
def test_d_no_cookie_no_redirect(tmp_path):
    """(d) GET / with no cookie → 200 classic home, no redirect."""
    app, monkey = _sticky_app(tmp_path)
    try:
        resp = _client(app).get("/", headers=_HTML_ACCEPT)
        assert resp.status_code == 200
        assert b"CLASSIC HOME" in resp.data
    finally:
        monkey.undo()


@pytest.mark.unit
def test_non_html_accept_not_redirected(tmp_path):
    """A machine client (Accept: application/json) hitting '/' must NOT bounce to
    the HTML shell even with the cookie set — guards the accept-html gate."""
    app, monkey = _sticky_app(tmp_path)
    try:
        resp = _client(app).get(
            "/", headers={"Accept": "application/json"},
            environ_overrides=_PREFER_COOKIE)
        assert resp.status_code == 200
    finally:
        monkey.undo()


@pytest.mark.unit
def test_classic_index_redirect_rejects_hostile_proxy_prefix(tmp_path):
    """The original #739 redirect shares the same forwarded-prefix boundary as
    /login and must not turn ``//host`` into a scheme-relative redirect."""
    app, monkey = _sticky_app(tmp_path)
    try:
        resp = _client(app).get(
            "/", headers=_HTML_ACCEPT,
            environ_overrides={**_PREFER_COOKIE, "SCRIPT_NAME": "//evil.example"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/app/"
    finally:
        monkey.undo()


# ---- anonymous login surface ------------------------------------------------

@pytest.mark.unit
def test_preferred_spa_redirects_anonymous_login_to_new_ui(tmp_path):
    """After logout, an anonymous HTML browser that still carries the durable
    preference must enter the SPA's logged-out tree instead of Classic login."""
    app, web_mod, monkey = _login_app(tmp_path)
    try:
        resp = _get_login(
            _client(app), web_mod, "/login", headers=_HTML_ACCEPT,
            environ_overrides=_PREFER_COOKIE,
        )
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/app/"
    finally:
        monkey.undo()


@pytest.mark.unit
def test_login_without_preference_keeps_classic_surface(tmp_path):
    """A new/no-cookie browser keeps the existing Classic login behavior."""
    app, web_mod, monkey = _login_app(tmp_path)
    try:
        resp = _get_login(_client(app), web_mod, "/login", headers=_HTML_ACCEPT)
        assert resp.status_code == 200
        assert resp.get_data(as_text=True) == "CLASSIC LOGIN"
    finally:
        monkey.undo()


@pytest.mark.unit
def test_preferred_spa_login_does_not_redirect_non_html_client(tmp_path):
    """Machine clients carrying a shared browser cookie must not be sent HTML."""
    app, web_mod, monkey = _login_app(tmp_path)
    try:
        resp = _get_login(
            _client(app), web_mod, "/login",
            headers={"Accept": "application/json"},
            environ_overrides=_PREFER_COOKIE,
        )
        assert resp.status_code == 200
        assert resp.get_data(as_text=True) == "CLASSIC LOGIN"
    finally:
        monkey.undo()


@pytest.mark.unit
def test_preferred_spa_login_stays_classic_when_spa_disabled(tmp_path):
    """The preference cannot redirect into a disabled/unavailable SPA shell."""
    app, web_mod, monkey = _login_app(tmp_path)
    monkey.setenv("CWNG_SPA", "0")
    try:
        resp = _get_login(
            _client(app), web_mod, "/login", headers=_HTML_ACCEPT,
            environ_overrides=_PREFER_COOKIE,
        )
        assert resp.status_code == 200
        assert resp.get_data(as_text=True) == "CLASSIC LOGIN"
    finally:
        monkey.undo()


@pytest.mark.unit
def test_preferred_spa_login_redirect_preserves_reverse_proxy_subpath(tmp_path):
    """url_for must keep the mount prefix; a hardcoded /app breaks #571."""
    app, web_mod, monkey = _login_app(tmp_path)
    try:
        resp = _get_login(
            _client(app), web_mod, "/login", headers=_HTML_ACCEPT,
            environ_overrides={**_PREFER_COOKIE, "SCRIPT_NAME": "/cwa"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/cwa/app/"
    finally:
        monkey.undo()


@pytest.mark.unit
def test_preferred_spa_login_ignores_next_and_preserves_subpath(tmp_path):
    """The handoff stays on the app-owned shell even when login has a ``next``
    target; the sanitized reverse-proxy prefix is the only preserved input."""
    app, web_mod, monkey = _login_app(tmp_path)
    try:
        resp = _get_login(
            _client(app), web_mod, "/login?next=%2Fcwa%2Fadmin",
            headers=_HTML_ACCEPT,
            environ_overrides={**_PREFER_COOKIE, "SCRIPT_NAME": "/cwa"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/cwa/app/"
    finally:
        monkey.undo()


@pytest.mark.unit
def test_preferred_spa_login_rejects_off_site_next_destination(tmp_path):
    """An attacker-controlled next= must never turn /login -> /app into an
    external redirect. The fixed SPA destination contains no hostile target."""
    app, web_mod, monkey = _login_app(tmp_path)
    try:
        resp = _get_login(
            _client(app), web_mod,
            "/login?next=https%3A%2F%2Fevil.example%2Fsteal",
            headers=_HTML_ACCEPT, environ_overrides=_PREFER_COOKIE,
        )
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/app/"
        assert "evil.example" not in resp.headers["Location"]
    finally:
        monkey.undo()


@pytest.mark.unit
@pytest.mark.parametrize("bad_prefix", [
    "//evil.example",
    "/../evil.example",
    "/a b",
    '/a"><script>evil</script>',
])
def test_preferred_spa_login_rejects_hostile_proxy_prefix(tmp_path, bad_prefix):
    """A trusted-prefix header still enters request.script_root. The redirect
    must use the SPA sanitizer rather than letting url_for emit //host/app/."""
    app, web_mod, monkey = _login_app(tmp_path)
    try:
        resp = _get_login(
            _client(app), web_mod, "/login", headers=_HTML_ACCEPT,
            environ_overrides={**_PREFER_COOKIE, "SCRIPT_NAME": bad_prefix},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/app/"
        assert "evil.example" not in resp.headers["Location"]
    finally:
        monkey.undo()


# ---- source pins: template gating + web.py wiring ----

@pytest.mark.unit
def test_e_layout_gates_banner_on_prefer_cookie():
    """(e) layout.html must hide the 'Try the new UI' banner when the preference
    cookie is set, and flip the header pill to 'Back to New UI'."""
    src = _LAYOUT.read_text()
    assert "request.cookies.get('cwng_prefer_spa') != '1'" in src, (
        "banner {% if %} not gated on cwng_prefer_spa absence")
    assert "Back to New UI" in src, "pill relabel not added"
    assert "request.cookies.get('cwng_prefer_spa') == '1'" in src, (
        "pill label not conditioned on the preference cookie")


@pytest.mark.unit
def test_web_index_wires_sticky_helpers():
    """web.py:index must clear on cwng_feedback and call the redirect helper —
    pins that the stand-in '/' route above mirrors production."""
    src = _WEB.read_text()
    assert "spa.clear_prefer_spa_cookie" in src
    assert "spa.classic_index_redirects_to_spa" in src
    assert "cwng_feedback" in src
