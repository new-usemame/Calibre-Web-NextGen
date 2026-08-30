# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#739/#908: the SPA is the default UI and Classic is a sticky opt-out.

A cookie-less browser and the old ``cwng_prefer_spa=1`` population both use the
SPA. Leaving through ``?cwng_feedback=newui`` stores ``cwng_prefer_classic=1``;
only the marked Classic-nav action clears that opt-out so Classic -> SPA ->
Classic can round-trip indefinitely; ordinary SPA deep links preserve it.
Redirects are limited to explicit browser-document HTML requests,
because wildcard or missing Accept headers are ordinary machine-client traffic.

Most sticky-cookie cases exercise the REAL spa.py helpers through a minimal
Flask app whose '/' route mirrors web.py:index. Cases whose ordering or side
effects matter mount the real web blueprint and patch only its final rendering
and environment boundaries; a stand-in cannot prove those contracts. The
SPA-shell cookie (test a) is hit over HTTP on the real spa blueprint; the
template gating (test e) is a source-pin.

Cookie mechanics: the test client is created with use_cookies=False so its own
(empty) cookie jar doesn't clobber the HTTP_COOKIE we inject per-request, and we
read Set-Cookie straight off resp.headers — so nothing depends on the
set_cookie() test-client API, which changed signature across the supported Flask
range (1.x–3.x).
"""
import pathlib
import inspect
import html as html_lib
import re
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock, patch

import flask
import pytest

import cps.spa as spa_mod

_REPO = pathlib.Path(__file__).resolve().parents[2]
_LAYOUT = _REPO / "cps" / "templates" / "layout.html"
_WEB = _REPO / "cps" / "web.py"

_HTML_ACCEPT = {"Accept": "text/html,application/xhtml+xml"}
_PREFER_COOKIE = {"HTTP_COOKIE": "cwng_prefer_spa=1"}
_CLASSIC_COOKIE = {"HTTP_COOKIE": "cwng_prefer_classic=1"}


def _seed_bundle(tmp_path):
    """A minimal built index.html so the shell serves 200 (the Fast CI job never
    runs the Vite build). Mirrors the test_spa_shell.py / test_571 fixture."""
    (tmp_path / "index.html").write_text(
        "<!doctype html><html><head><title>Calibre-Web NextGen</title>"
        "</head><body><div id=root></div></body></html>")
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
            spa_mod.stamp_prefer_classic_cookie(resp)
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


def _unauthorized_login_app(tmp_path):
    """Real login manager + protected route feeding the real web login route."""
    from cps.cw_login import LoginManager, login_required

    app, web_mod, monkey = _login_app(tmp_path)
    login_manager = LoginManager(app)
    login_manager.login_view = "web.login"

    @login_manager.user_loader
    def _load_user(_user_id):
        return None

    @app.route("/private-book")
    @login_required
    def _private_book():
        return "PRIVATE"

    return app, web_mod, monkey


def _no_js_bridge_app(tmp_path, authenticated):
    """Real SPA/web blueprints and LoginManager for the no-JS state machine."""
    from cps.cw_login import LoginManager

    app, web_mod, monkey = _login_app(tmp_path)
    app.config["SESSION_PROTECTION"] = None
    login_manager = LoginManager(app)
    login_manager.login_view = "web.login"
    user = MagicMock()
    user.is_authenticated = True
    user.is_active = True
    user.is_anonymous = False
    user.get_id.return_value = "7"
    user.role_admin.return_value = False

    @login_manager.user_loader
    def _load_user(user_id, _random, _session_key):
        return user if authenticated and user_id == "7" else None

    return app, web_mod, monkey


_META_REFRESH_URL = re.compile(
    r'<meta[^>]+http-equiv="refresh"[^>]+content="0;url=([^\"]+)"',
    re.IGNORECASE,
)


def _walk_no_js_bridge(client, start="/app/", max_steps=12):
    """Follow HTTP redirects and the shell's no-JS meta refresh.

    Return the first Classic response. Re-entering a URL proves a redirect
    cycle instead of relying on Playwright's eventual navigation timeout.
    """
    current = start
    visited = []
    for _step in range(max_steps):
        assert current not in visited, (
            "no-JS bridge entered a redirect loop: "
            + " -> ".join([*visited, current])
        )
        visited.append(current)
        response = client.get(current, headers=_HTML_ACCEPT)
        body = response.get_data(as_text=True)
        if "CLASSIC HOME" in body or "CLASSIC LOGIN" in body:
            return response, visited
        if response.status_code in (301, 302, 303, 307, 308):
            current = response.headers["Location"]
            continue
        refresh = _META_REFRESH_URL.search(body)
        assert refresh is not None, (
            f"no-JS bridge stopped on non-Classic response {response.status_code} "
            f"at {current}"
        )
        current = html_lib.unescape(refresh.group(1))
    pytest.fail(
        "no-JS bridge did not reach Classic within "
        f"{max_steps} requests: {' -> '.join(visited)}"
    )


def _index_app(tmp_path):
    """App with the real SPA and web blueprints for behavioral index tests."""
    import cps.web as web_mod

    monkey = _seed_bundle(tmp_path)
    app = flask.Flask(__name__)
    app.config.update(SECRET_KEY="test", TESTING=True)
    _mirror_prod_session_config(app)
    app.register_blueprint(spa_mod.spa)
    app.register_blueprint(web_mod.web)
    return app, web_mod, monkey


def _get_login(client, web_mod, *args, login_type=0,
               reverse_proxy_login=False, **kwargs):
    anonymous = MagicMock()
    anonymous.is_authenticated = False
    with patch.object(web_mod, "current_user", anonymous), \
         patch.object(web_mod, "render_login", return_value="CLASSIC LOGIN"), \
         patch.object(web_mod.config, "config_login_type", login_type, create=True), \
         patch.object(web_mod.config, "config_allow_reverse_proxy_header_login",
                      reverse_proxy_login, create=True), \
         patch.object(web_mod.config, "config_disable_standard_login", False,
                      create=True), \
         patch.object(web_mod.config, "config_enable_oauth_auto_forward", False,
                      create=True):
        return client.get(*args, **kwargs)


def _client(app):
    """use_cookies=False: we inject cookies per-request via environ_overrides and
    read Set-Cookie off resp.headers, sidestepping the version-volatile
    test-client cookie API."""
    return app.test_client(use_cookies=False)


def _set_cookie(resp):
    return ", ".join(resp.headers.getlist("Set-Cookie"))


def _call_real_index(path, tmp_path, *, headers=None, environ_overrides=None):
    """Drive the unwrapped production index branch with its rendering boundary
    stubbed, so coverage and behavior both include cps.web.index itself."""
    import cps.web as web_mod

    monkey = _seed_bundle(tmp_path)
    app = flask.Flask(__name__)
    _mirror_prod_session_config(app)
    anonymous = MagicMock()
    anonymous.is_authenticated = False
    with app.test_request_context(
            path, headers=headers, environ_overrides=environ_overrides), \
         patch.object(web_mod, "current_user", anonymous), \
         patch.object(web_mod, "render_books_list", return_value="CLASSIC HOME"):
        result = inspect.unwrap(web_mod.index)(1)
        response = app.make_response(result)
    monkey.undo()
    return response


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
def test_explicit_spa_choice_clears_classic_opt_out(tmp_path):
    """Only the marked Classic-nav action revokes the durable opt-out.

    It redirects to the clean shell URL so refreshing or bookmarking the SPA
    does not retain a preference-mutating query parameter.
    """
    app, monkey = _sticky_app(tmp_path)
    try:
        client = app.test_client()
        client.set_cookie(spa_mod.PREFER_CLASSIC_COOKIE, "1")

        resp = client.get(
            "/app/?cwng_switch=spa", headers=_HTML_ACCEPT)

        assert resp.status_code == 302
        assert resp.headers["Location"] == "/app/"
        sc = _set_cookie(resp)
        assert "cwng_prefer_classic=" in sc
        assert "Max-Age=0" in sc
        assert "cwng_prefer_spa=1" in sc
        assert client.get_cookie(spa_mod.PREFER_CLASSIC_COOKIE) is None

        shell = client.get(resp.headers["Location"], headers=_HTML_ACCEPT)
        assert shell.status_code == 200
        assert not any(
            value.startswith("cwng_prefer_classic=")
            for value in shell.headers.getlist("Set-Cookie")
        )

        home = client.get("/", headers=_HTML_ACCEPT)
        assert home.status_code == 302
        assert home.headers["Location"] == "/app/"
    finally:
        monkey.undo()


@pytest.mark.unit
def test_spa_deep_link_preserves_explicit_classic_choice(tmp_path):
    """Visiting shared SPA content is not consent to revoke Classic.

    Assert the response cookie mutation, the browser's resulting cookie jar,
    and the next preference-routed navigation. A header-only assertion could
    miss a differently-shaped deletion; a route-only assertion could miss a
    stale duplicate cookie that happens to win in this test client.
    """
    app, monkey = _sticky_app(tmp_path)
    try:
        client = app.test_client()
        client.set_cookie(spa_mod.PREFER_CLASSIC_COOKIE, "1")

        deep_link = client.get("/app/book/5", headers=_HTML_ACCEPT)

        assert deep_link.status_code == 200
        set_cookies = deep_link.headers.getlist("Set-Cookie")
        assert any(
            value.startswith("cwng_prefer_spa=1;")
            for value in set_cookies
        )
        assert not any(
            value.startswith("cwng_prefer_classic=")
            for value in set_cookies
        )
        preserved = client.get_cookie(spa_mod.PREFER_CLASSIC_COOKIE)
        assert preserved is not None
        assert preserved.value == "1"

        classic_home = client.get("/", headers=_HTML_ACCEPT)
        assert classic_home.status_code == 200
        assert classic_home.get_data(as_text=True) == "CLASSIC HOME"
        assert client.get_cookie(spa_mod.PREFER_CLASSIC_COOKIE).value == "1"
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
def test_explicit_spa_choice_url_uses_sanitized_mount_prefix(tmp_path):
    """The Classic nav action shares spa_shell_url's #571 prefix sanitizer."""
    app, monkey = _spa_only_app(tmp_path)
    try:
        with app.test_request_context(
                "/", environ_overrides={"SCRIPT_NAME": "/cwa"}):
            assert spa_mod.spa_shell_choice_url() == (
                "/cwa/app/?cwng_switch=spa")

        with app.test_request_context(
                "/", environ_overrides={"SCRIPT_NAME": "//evil.example"}):
            assert spa_mod.spa_shell_choice_url() == (
                "/app/?cwng_switch=spa")
    finally:
        monkey.undo()


@pytest.mark.unit
def test_b_legacy_spa_cookie_still_redirects(tmp_path):
    """Existing ``cwng_prefer_spa=1`` users keep their SPA experience."""
    app, monkey = _sticky_app(tmp_path)
    try:
        resp = _client(app).get(
            "/", headers=_HTML_ACCEPT, environ_overrides=_PREFER_COOKIE)
        assert resp.status_code == 302
        assert resp.headers["Location"].rstrip("/").endswith("/app")
    finally:
        monkey.undo()


@pytest.mark.unit
def test_c_feedback_sets_classic_opt_out_and_does_not_redirect(tmp_path):
    """Leaving the SPA renders Classic, sets its durable opt-out, and deletes
    the legacy SPA preference so downgrades preserve the same choice."""
    app, monkey = _sticky_app(tmp_path)
    try:
        resp = _client(app).get(
            "/?cwng_feedback=newui",
            headers=_HTML_ACCEPT, environ_overrides=_PREFER_COOKIE)
        assert resp.status_code == 200
        sc = _set_cookie(resp)
        assert "cwng_prefer_classic=1" in sc
        assert "Max-Age=31536000" in sc
        assert "cwng_prefer_spa=" in sc
    finally:
        monkey.undo()


@pytest.mark.unit
def test_d_cookie_less_browser_redirects_to_spa(tmp_path):
    """The changed default: a fresh browser navigation enters the SPA."""
    app, monkey = _sticky_app(tmp_path)
    try:
        resp = _client(app).get("/", headers=_HTML_ACCEPT)
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/app/"
    finally:
        monkey.undo()


@pytest.mark.unit
@pytest.mark.parametrize("headers", [
    {},
    {"Accept": "*/*", "User-Agent": "curl/8.7.1"},
    {"Accept": "*/*", "User-Agent": "Wget/1.21.4"},
    {"Accept": "application/atom+xml,application/xml;q=0.9,*/*;q=0.1",
     "User-Agent": "Moon+ Reader Pro/9.6 (OPDS)"},
    {"Accept": "*/*", "User-Agent": "Kobo Touch/4.38.21908"},
    {"Accept": "application/json"},
])
def test_machine_client_header_sets_are_not_redirected(tmp_path, headers):
    """Missing/wildcard/non-HTML Accept sets used by curl, wget, OPDS readers,
    and Kobo must retain the classic endpoint response after SPA becomes default."""
    app, monkey = _sticky_app(tmp_path)
    try:
        resp = _client(app).get("/", headers=headers)
        assert resp.status_code == 200
        assert b"CLASSIC HOME" in resp.data
    finally:
        monkey.undo()


@pytest.mark.unit
def test_non_document_fetch_with_html_accept_is_not_redirected(tmp_path):
    """An HTML fetch for a subresource is not a top-level browser navigation."""
    app, monkey = _sticky_app(tmp_path)
    try:
        resp = _client(app).get(
            "/", headers={"Accept": "text/html", "Sec-Fetch-Dest": "empty"})
        assert resp.status_code == 200
    finally:
        monkey.undo()


@pytest.mark.unit
@pytest.mark.parametrize("headers", [
    {"Accept": "text/html;q=0,*/*;q=1"},
    {"Accept": "text/html", "Sec-Fetch-Dest": "document",
     "Sec-Fetch-Mode": "cors"},
])
def test_non_navigating_or_explicitly_rejected_html_is_not_redirected(
        tmp_path, headers):
    app, monkey = _sticky_app(tmp_path)
    try:
        resp = _client(app).get("/", headers=headers)
        assert resp.status_code == 200
        assert b"CLASSIC HOME" in resp.data
    finally:
        monkey.undo()


@pytest.mark.unit
def test_fetch_metadata_document_navigation_redirects(tmp_path):
    app, monkey = _sticky_app(tmp_path)
    try:
        resp = _client(app).get("/", headers={
            "Accept": "text/html,application/xhtml+xml",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
        })
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/app/"
    finally:
        monkey.undo()


@pytest.mark.unit
def test_classic_opt_out_sticks_across_fresh_request(tmp_path):
    app, monkey = _sticky_app(tmp_path)
    try:
        resp = _client(app).get(
            "/", headers=_HTML_ACCEPT, environ_overrides=_CLASSIC_COOKIE)
        assert resp.status_code == 200
        assert b"CLASSIC HOME" in resp.data
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
def test_login_without_preference_uses_spa_surface(tmp_path):
    """A new/no-cookie browser uses the SPA login tree by default."""
    app, web_mod, monkey = _login_app(tmp_path)
    try:
        resp = _get_login(_client(app), web_mod, "/login", headers=_HTML_ACCEPT)
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/app/"
    finally:
        monkey.undo()


@pytest.mark.unit
@pytest.mark.parametrize(("login_type", "reverse_proxy_login"), [
    (0, False),
    (spa_mod.constants.LOGIN_OAUTH, False),
    (spa_mod.constants.LOGIN_LDAP, False),
    (0, True),
])
def test_login_default_is_auth_capability_aware(
        tmp_path, login_type, reverse_proxy_login):
    """Every configured authentication mode now has a working SPA path."""
    app, web_mod, monkey = _login_app(tmp_path)
    try:
        resp = _get_login(
            _client(app), web_mod, "/login", headers=_HTML_ACCEPT,
            login_type=login_type,
            reverse_proxy_login=reverse_proxy_login,
        )
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/app/"
    finally:
        monkey.undo()


@pytest.mark.unit
@pytest.mark.parametrize(("login_type", "reverse_proxy_login"), [
    (spa_mod.constants.LOGIN_LDAP, False),
    (0, True),
])
def test_auth_mode_does_not_disable_authenticated_index_spa(
        tmp_path, login_type, reverse_proxy_login):
    """An authenticated user's `/` routing stays on the SPA in either mode."""
    app, monkey = _sticky_app(tmp_path)
    try:
        with patch.object(spa_mod.config, "config_login_type", login_type,
                          create=True), \
             patch.object(spa_mod.config,
                          "config_allow_reverse_proxy_header_login",
                          reverse_proxy_login, create=True):
            resp = _client(app).get("/", headers=_HTML_ACCEPT)
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/app/"
    finally:
        monkey.undo()


@pytest.mark.unit
@pytest.mark.parametrize(("path", "headers", "cookie", "status"), [
    ("/", _HTML_ACCEPT, None, 302),
    ("/", _HTML_ACCEPT, _CLASSIC_COOKIE, 200),
    ("/", {"Accept": "*/*", "User-Agent": "curl/8.7.1"}, None, 200),
    ("/?cwng_feedback=newui", _HTML_ACCEPT, _PREFER_COOKIE, 200),
])
def test_production_web_index_executes_the_preference_contract(
        tmp_path, path, headers, cookie, status):
    response = _call_real_index(
        path, tmp_path, headers=headers, environ_overrides=cookie)

    assert response.status_code == status
    if status == 302:
        assert response.headers["Location"] == "/app/"
    else:
        assert response.get_data(as_text=True) == "CLASSIC HOME"
    if "cwng_feedback" in path:
        cookies = _set_cookie(response)
        assert "cwng_prefer_classic=1" in cookies
        assert "cwng_prefer_spa=" in cookies


@pytest.mark.unit
def test_real_index_flashes_architecture_warning_only_when_classic_renders(
        tmp_path):
    """#1959: redirects must not queue a Classic-only warning in the session.

    Exercise the mounted production blueprint rather than the sticky stand-in:
    both explicit Classic paths still flash, while the default SPA redirect
    leaves ``session['_flashes']`` empty.
    """
    app, web_mod, monkey = _index_app(tmp_path)
    admin = MagicMock()
    admin.is_authenticated = True
    admin.role_admin.return_value = True
    warning = "Unsupported architecture"

    try:
        with patch.object(web_mod, "current_user", admin), \
             patch.object(web_mod.helper, "check_architecture",
                          return_value=warning), \
             patch.object(web_mod, "render_books_list",
                          return_value="CLASSIC HOME"), \
             patch.object(web_mod.config, "config_anonbrowse", 1,
                          create=True), \
             patch.object(web_mod.config,
                          "config_allow_reverse_proxy_header_login", False,
                          create=True):
            redirect_client = app.test_client()
            redirected = redirect_client.get("/", headers=_HTML_ACCEPT)
            assert redirected.status_code == 302
            assert redirected.headers["Location"] == "/app/"
            with redirect_client.session_transaction() as sess:
                assert sess.get("_flashes", []) == []

            feedback_client = app.test_client()
            feedback = feedback_client.get(
                "/?cwng_feedback=newui", headers=_HTML_ACCEPT)
            assert feedback.status_code == 200
            with feedback_client.session_transaction() as sess:
                assert sess.get("_flashes") == [
                    ("cwa_arch_warning", warning)]

            classic_client = app.test_client()
            classic_client.set_cookie("cwng_prefer_classic", "1")
            classic = classic_client.get("/", headers=_HTML_ACCEPT)
            assert classic.status_code == 200
            with classic_client.session_transaction() as sess:
                assert sess.get("_flashes") == [
                    ("cwa_arch_warning", warning)]
    finally:
        monkey.undo()


@pytest.mark.unit
def test_classic_opt_out_keeps_anonymous_login_classic(tmp_path):
    app, web_mod, monkey = _login_app(tmp_path)
    try:
        resp = _get_login(
            _client(app), web_mod, "/login", headers=_HTML_ACCEPT,
            environ_overrides=_CLASSIC_COOKIE,
        )
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
            environ_overrides={"SCRIPT_NAME": "/cwa"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/cwa/app/"
    finally:
        monkey.undo()


@pytest.mark.unit
def test_preferred_spa_login_preserves_safe_next_on_app_owned_shell(tmp_path):
    """A safe next rides as data; it never replaces the fixed shell target."""
    app, web_mod, monkey = _login_app(tmp_path)
    try:
        resp = _get_login(
            _client(app), web_mod, "/login?next=%2Fcwa%2Fbook%2F42",
            headers=_HTML_ACCEPT,
            environ_overrides={**_PREFER_COOKIE, "SCRIPT_NAME": "/cwa"},
        )
        assert resp.status_code == 302
        destination = urlsplit(resp.headers["Location"])
        assert destination.scheme == ""
        assert destination.netloc == ""
        assert destination.path == "/cwa/app/"
        assert parse_qs(destination.query) == {"next": ["/cwa/book/42"]}
    finally:
        monkey.undo()


@pytest.mark.unit
@pytest.mark.parametrize("hostile_next", [
    "//evil.example/steal",
    "https://evil.example/steal",
    "/\\evil.example/steal",
    "/book\\evil.example",
    "/different-prefix/book/42",
])
def test_preferred_spa_login_next_cannot_change_app_owned_destination(
        tmp_path, hostile_next):
    """Hostile next values remain encoded data for the SPA sanitizer.

    Scheme, authority, and path are derived only from the sanitized mount
    prefix; neither an off-origin value nor a same-origin path outside this
    instance's subpath can influence where the browser is actually sent.
    """
    app, web_mod, monkey = _login_app(tmp_path)
    try:
        resp = _get_login(
            _client(app), web_mod,
            "/login", query_string={"next": hostile_next},
            headers=_HTML_ACCEPT,
            environ_overrides={**_PREFER_COOKIE, "SCRIPT_NAME": "/cwa"},
        )
        assert resp.status_code == 302
        destination = urlsplit(resp.headers["Location"])
        assert destination.scheme == ""
        assert destination.netloc == ""
        assert destination.path == "/cwa/app/"
        assert parse_qs(destination.query) == {"next": [hostile_next]}
    finally:
        monkey.undo()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("prefix", "next_url", "expected"),
    [
        ("", "/?cwng_feedback=newui", True),
        ("/cwa", "/cwa/?cwng_feedback=newui", True),
        ("", "//evil.example/?cwng_feedback=newui", False),
        ("", "https://evil.example/?cwng_feedback=newui", False),
        ("", "/\\evil?cwng_feedback=newui", False),
        ("/cwa", "/?cwng_feedback=newui", False),
        ("/cwa", "/other/?cwng_feedback=newui", False),
        ("/cwa", "/cwa/?cwng_feedback=other", False),
        ("/cwa", "/cwa/?cwng_feedback=newui&extra=1", False),
        ("/cwa", "/cwa/?cwng_feedback=newui#fragment", False),
    ],
)
def test_classic_fallback_next_marker_is_fixed_and_prefix_scoped(
        prefix, next_url, expected):
    """Only the server-emitted, app-owned no-JS marker selects Classic login."""
    app = flask.Flask(__name__)
    with app.test_request_context("/login", environ_overrides={
            "SCRIPT_NAME": prefix}):
        assert spa_mod.classic_fallback_requested_from_next(next_url) is expected


@pytest.mark.unit
def test_real_unauthorized_login_redirect_drains_classic_flash(tmp_path):
    """#1959: the SPA login cannot display Flask-Login's login message.

    Drive the actual login manager through a protected route, confirm it queues
    the message, then follow its real /login?next redirect and prove that the
    SPA handoff preserves next without leaving the flash in the session.
    """
    app, web_mod, monkey = _unauthorized_login_app(tmp_path)
    try:
        with patch.object(web_mod.config, "config_login_type", 0, create=True), \
             patch.object(web_mod.config,
                          "config_allow_reverse_proxy_header_login", False,
                          create=True), \
             patch.object(web_mod.config, "config_disable_standard_login",
                          False, create=True), \
             patch.object(web_mod.config,
                          "config_enable_oauth_auto_forward", False,
                          create=True):
            client = app.test_client()
            unauthorized = client.get("/private-book", headers=_HTML_ACCEPT)
            assert unauthorized.status_code == 302
            login_location = urlsplit(unauthorized.headers["Location"])
            assert login_location.path == "/login"
            assert parse_qs(login_location.query) == {
                "next": ["/private-book"]}
            with client.session_transaction() as sess:
                assert sess.get("_flashes") == [
                    ("message", "Please log in to access this page.")]

            spa_login = client.get(
                unauthorized.headers["Location"], headers=_HTML_ACCEPT)
            assert spa_login.status_code == 302
            destination = urlsplit(spa_login.headers["Location"])
            assert destination.path == "/app/"
            assert parse_qs(destination.query) == {
                "next": ["/private-book"]}
            with client.session_transaction() as sess:
                assert sess.get("_flashes", []) == []
    finally:
        monkey.undo()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("anonymous_browsing", "authenticated", "terminal_body"),
    [
        (1, False, "CLASSIC HOME"),
        (0, False, "CLASSIC LOGIN"),
        (1, True, "CLASSIC HOME"),
        (0, True, "CLASSIC HOME"),
    ],
)
def test_no_js_bridge_reaches_sticky_classic_for_every_auth_combination(
        tmp_path, anonymous_browsing, authenticated, terminal_body):
    """A no-JS shell must terminate on a usable Classic surface.

    This walks the production route chain that CI exposed: shell meta refresh
    -> feedback index -> login-required redirect -> login preference routing.
    In particular, an unauthenticated visitor with anonymous browsing disabled
    must not be sent back to the SPA with the fallback marker nested in ``next``.
    The other three combinations pin the already-correct direct Classic path.
    """
    app, web_mod, monkey = _no_js_bridge_app(tmp_path, authenticated)
    displayed_flashes = []

    def _classic_login():
        displayed_flashes.extend(flask.get_flashed_messages(with_categories=True))
        return "CLASSIC LOGIN"

    try:
        with patch.object(web_mod.config, "config_anonbrowse",
                          anonymous_browsing, create=True), \
             patch.object(web_mod.config, "config_login_type", 0,
                          create=True), \
             patch.object(web_mod.config,
                          "config_allow_reverse_proxy_header_login", False,
                          create=True), \
             patch.object(web_mod.config, "config_disable_standard_login",
                          False, create=True), \
             patch.object(web_mod.config,
                          "config_enable_oauth_auto_forward", False,
                          create=True), \
             patch.object(web_mod, "render_books_list",
                          return_value="CLASSIC HOME"), \
             patch.object(web_mod, "render_login",
                          side_effect=_classic_login):
            client = app.test_client()
            if authenticated:
                with client.session_transaction() as sess:
                    sess["_user_id"] = "7"
                    sess["_fresh"] = True
                    sess["_id"] = "test-session"
                    sess["_random"] = "test-random"

            response, visited = _walk_no_js_bridge(client)

            assert response.get_data(as_text=True) == terminal_body
            assert "/?cwng_feedback=newui" in visited
            cookies = _set_cookie(response)
            assert "cwng_prefer_classic=1" in cookies
            assert "cwng_prefer_spa=" in cookies

            # A fresh navigation with only the cookie jar retained must stay on
            # Classic; authenticated /login redirects through the real index.
            fresh, _ = _walk_no_js_bridge(client, start="/login")
            assert fresh.get_data(as_text=True).startswith("CLASSIC ")

            if not anonymous_browsing and not authenticated:
                assert displayed_flashes == [
                    ("message", "Please log in to access this page.")]
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
def test_e_layout_has_plain_return_affordance_without_banner():
    """Classic has one quiet return affordance; the opt-in nudge is gone."""
    src = _LAYOUT.read_text()
    assert 'id="cwng-newui-banner"' not in src
    assert "Your classic view stays the default until you switch." not in src
    assert "cwng_newui_banner_dismissed" not in src
    assert "Back to New UI" in src
    assert "Switch to New UI" not in src
    assert 'href="{{ cwng_spa_choice_url() }}"' in src


@pytest.mark.unit
def test_web_index_wires_sticky_helpers():
    """web.py:index must clear on cwng_feedback and call the redirect helper —
    pins that the stand-in '/' route above mirrors production."""
    src = _WEB.read_text()
    assert "spa.stamp_prefer_classic_cookie" in src
    assert "spa.clear_prefer_spa_cookie" in src
    assert "spa.classic_index_redirects_to_spa" in src
    assert "cwng_feedback" in src
