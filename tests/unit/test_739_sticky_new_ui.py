# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#739/#908: the SPA is always-default; Classic is a session escape hatch.

A cookie-less browser and the old ``cwng_prefer_spa=1`` population both use the
SPA. Leaving through ``?cwng_feedback=newui`` marks the signed Flask session for
Classic without creating a durable cookie. A login, logout, fresh browser session,
or the marked Classic-to-SPA action clears that escape hatch. The obsolete
``cwng_prefer_classic=1`` cookie is ignored and actively expired.
Redirects are limited to explicit browser-document HTML requests,
because wildcard or missing Accept headers are ordinary machine-client traffic.

Most session-selection cases exercise the REAL spa.py helpers through a minimal
Flask app whose '/' route mirrors web.py:index. Cases whose ordering or side
effects matter mount the real web blueprint and patch only its final rendering
and environment boundaries; a stand-in cannot prove those contracts. The
SPA-shell cookie (test a) is hit over HTTP on the real spa blueprint; the
template gating (test e) is a source-pin.

Cookie migration cases use a no-jar client so the injected legacy Cookie header
reaches the request unchanged. Session behavior uses Flask's normal test-client
jar and ``session_transaction`` so it exercises the signed session cookie.
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
    UI-session wiring: cwng_feedback marks the session, otherwise redirect when
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
            spa_mod.prefer_classic_for_session()
            spa_mod.clear_prefer_spa_cookie(resp)
            return resp
        if spa_mod.classic_index_redirects_to_spa():
            return flask.redirect(spa_mod.spa_shell_url())
        return "CLASSIC HOME"

    return app, monkey


def _remembered_sticky_app(tmp_path):
    """Sticky marker app using the production login/session-protection stack."""
    from cps.MyLoginManager import MyLoginManager
    from cps.cw_login import current_user, login_user

    app, monkey = _sticky_app(tmp_path)
    app.config["SESSION_PROTECTION"] = "strong"
    login_manager = MyLoginManager(app)
    user = MagicMock()
    user.is_authenticated = True
    user.is_active = True
    user.is_anonymous = False
    user.get_id.return_value = "7"
    user.nickname = "remembered-user"

    @login_manager.user_loader
    def _load_user(user_id, _random, _session_key):
        return user if user_id == "7" else None

    @app.route("/test-remember-login")
    def _test_remember_login():
        assert login_user(user, remember=True)
        return "LOGGED IN"

    @app.route("/test-auth-state")
    def _test_auth_state():
        return flask.jsonify(authenticated=current_user.is_authenticated)

    return app, user, monkey


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
    app.config["SECRET_KEY"] = "test"
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
def test_explicit_spa_choice_clears_classic_session_and_legacy_cookie(tmp_path):
    """The marked Classic-nav action revokes the transient escape hatch.

    It redirects to the clean shell URL so refreshing or bookmarking the SPA
    does not retain a preference-mutating query parameter.
    """
    app, monkey = _sticky_app(tmp_path)
    try:
        client = app.test_client()
        client.set_cookie(spa_mod.PREFER_CLASSIC_COOKIE, "1")
        with client.session_transaction() as sess:
            sess[spa_mod.CLASSIC_SESSION_KEY] = True

        resp = client.get(
            "/app/?cwng_switch=spa", headers=_HTML_ACCEPT)

        assert resp.status_code == 302
        assert resp.headers["Location"] == "/app/"
        sc = _set_cookie(resp)
        assert "cwng_prefer_classic=" in sc
        assert "Max-Age=0" in sc
        assert "cwng_prefer_spa=1" in sc
        assert client.get_cookie(spa_mod.PREFER_CLASSIC_COOKIE) is None
        with client.session_transaction() as sess:
            assert spa_mod.CLASSIC_SESSION_KEY not in sess

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
def test_spa_deep_link_preserves_classic_for_current_session(tmp_path):
    """Visiting shared SPA content is not an explicit surface-selection action."""
    app, monkey = _sticky_app(tmp_path)
    try:
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[spa_mod.CLASSIC_SESSION_KEY] = True

        deep_link = client.get("/app/book/5", headers=_HTML_ACCEPT)

        assert deep_link.status_code == 200
        set_cookies = deep_link.headers.getlist("Set-Cookie")
        assert any(
            value.startswith("cwng_prefer_spa=1;")
            for value in set_cookies
        )
        with client.session_transaction() as sess:
            assert sess[spa_mod.CLASSIC_SESSION_KEY] is True

        classic_home = client.get("/", headers=_HTML_ACCEPT)
        assert classic_home.status_code == 200
        assert classic_home.get_data(as_text=True) == "CLASSIC HOME"
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
def test_c_feedback_sets_session_scoped_classic_escape_hatch(tmp_path):
    """The fallback marks Classic without changing login-owned permanence."""
    app, monkey = _sticky_app(tmp_path)
    try:
        client = app.test_client()
        client.set_cookie(spa_mod.PREFER_SPA_COOKIE, "1")
        with client.session_transaction() as sess:
            sess.permanent = True
        resp = client.get("/?cwng_feedback=newui", headers=_HTML_ACCEPT)
        assert resp.status_code == 200
        cookies = resp.headers.getlist("Set-Cookie")
        assert any(value.startswith("cwng_prefer_spa=") for value in cookies)
        assert not any(value.startswith("cwng_prefer_classic=1") for value in cookies)
        session_cookie = next(value for value in cookies if value.startswith("session="))
        assert "Expires=" in session_cookie
        assert "Max-Age=" not in session_cookie
        with client.session_transaction() as sess:
            assert sess[spa_mod.CLASSIC_SESSION_KEY] is True
            assert sess.permanent is True
    finally:
        monkey.undo()


@pytest.mark.unit
def test_feedback_marker_preserves_real_remembered_login(tmp_path):
    """Regression: choosing Classic must not silently sign the user out.

    Use the real ``login_user(..., remember=True)`` and ``MyLoginManager``
    response hook. Changing the marker request's address exercises production
    strong-session-protection bookkeeping; the next request would clear both
    authentication and ``remember_token`` if the marker flipped permanence.
    """
    from cps import ub

    app, _user, monkey = _remembered_sticky_app(tmp_path)
    try:
        client = app.test_client()
        with patch.object(ub, "check_user_session", return_value=True):
            login_response = client.get("/test-remember-login")
        assert login_response.status_code == 200
        assert client.get_cookie("remember_token") is not None

        marker = client.get(
            "/?cwng_feedback=newui",
            headers=_HTML_ACCEPT,
            environ_overrides={"REMOTE_ADDR": "203.0.113.7"},
        )
        assert marker.status_code == 200
        assert not any(
            value.startswith("remember_token=;")
            for value in marker.headers.getlist("Set-Cookie")
        )
        assert client.get_cookie("remember_token") is not None

        authenticated = client.get(
            "/test-auth-state",
            environ_overrides={"REMOTE_ADDR": "203.0.113.7"},
        )
        assert authenticated.get_json() == {"authenticated": True}
        assert not any(
            value.startswith("remember_token=;")
            for value in authenticated.headers.getlist("Set-Cookie")
        )
        assert client.get_cookie("remember_token") is not None
    finally:
        monkey.undo()


@pytest.mark.unit
def test_remember_cookie_restore_clears_classic_escape_hatch(tmp_path):
    """A remember-cookie load is the fresh browser-session boundary."""
    from cps import ub
    from cps.cw_login.signals import user_loaded_from_cookie

    app, user, monkey = _remembered_sticky_app(tmp_path)
    restored_users = []

    def _observe_cookie_restore(_sender, **extra):
        restored_users.append(extra["user"])

    user_loaded_from_cookie.connect(_observe_cookie_restore, sender=app, weak=False)
    try:
        login_client = app.test_client()
        with patch.object(ub, "check_user_session", return_value=True):
            assert login_client.get("/test-remember-login").status_code == 200
        remember_cookie = login_client.get_cookie("remember_token")
        assert remember_cookie is not None

        restored_client = app.test_client()
        restored_client.set_cookie("remember_token", remember_cookie.value)
        with restored_client.session_transaction() as sess:
            sess[spa_mod.CLASSIC_SESSION_KEY] = True

        restored = restored_client.get("/test-auth-state")
        assert restored.get_json() == {"authenticated": True}
        assert restored_users == [user]
        with restored_client.session_transaction() as sess:
            assert spa_mod.CLASSIC_SESSION_KEY not in sess
    finally:
        user_loaded_from_cookie.disconnect(_observe_cookie_restore, sender=app)
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
@pytest.mark.parametrize(
    ("prefix", "location", "deleted_paths"),
    [
        ("", "/app/", {"/"}),
        ("/cwa", "/cwa/app/", {"/", "/cwa"}),
    ],
)
def test_legacy_classic_cookie_is_ignored_and_deleted(
        tmp_path, prefix, location, deleted_paths):
    app, monkey = _sticky_app(tmp_path)
    try:
        resp = _client(app).get(
            "/", headers=_HTML_ACCEPT, environ_overrides={
                **_CLASSIC_COOKIE, "SCRIPT_NAME": prefix})
        assert resp.status_code == 302
        assert resp.headers["Location"] == location
        stale_deletions = [
            value for value in resp.headers.getlist("Set-Cookie")
            if value.startswith("cwng_prefer_classic=")
        ]
        assert all("Max-Age=0" in value for value in stale_deletions)
        assert {
            re.search(r"(?:^|; )Path=([^;]+)", value).group(1)
            for value in stale_deletions
        } == deleted_paths
    finally:
        monkey.undo()


@pytest.mark.unit
def test_deliberate_classic_switch_sticks_only_within_same_session(tmp_path):
    app, monkey = _sticky_app(tmp_path)
    try:
        client = app.test_client()
        switched = client.get(
            "/?cwng_feedback=newui", headers=_HTML_ACCEPT)
        assert switched.status_code == 200

        same_session = client.get("/", headers=_HTML_ACCEPT)
        assert same_session.status_code == 200
        assert same_session.get_data(as_text=True) == "CLASSIC HOME"

        fresh_session = app.test_client().get("/", headers=_HTML_ACCEPT)
        assert fresh_session.status_code == 302
        assert fresh_session.headers["Location"] == "/app/"
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
    """After logout, an anonymous HTML browser enters the SPA login tree."""
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
    ("/", _HTML_ACCEPT, _CLASSIC_COOKIE, 302),
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
        assert "cwng_prefer_classic=1" not in cookies
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
            with classic_client.session_transaction() as sess:
                sess[spa_mod.CLASSIC_SESSION_KEY] = True
            classic = classic_client.get("/", headers=_HTML_ACCEPT)
            assert classic.status_code == 200
            with classic_client.session_transaction() as sess:
                assert sess.get("_flashes") == [
                    ("cwa_arch_warning", warning)]
    finally:
        monkey.undo()


@pytest.mark.unit
def test_classic_session_keeps_anonymous_login_classic(tmp_path):
    app, web_mod, monkey = _login_app(tmp_path)
    try:
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[spa_mod.CLASSIC_SESSION_KEY] = True
        resp = _get_login(
            client, web_mod, "/login", headers=_HTML_ACCEPT,
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
def test_no_js_bridge_reaches_session_classic_for_every_auth_combination(
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
            assert "cwng_prefer_classic=1" not in cookies
            assert "cwng_prefer_spa=" in cookies
            with client.session_transaction() as sess:
                assert sess[spa_mod.CLASSIC_SESSION_KEY] is True
                assert sess.permanent is False

            # A navigation in the same signed browser session must stay on
            # Classic; authenticated /login redirects through the real index.
            fresh, _ = _walk_no_js_bridge(client, start="/login")
            assert fresh.get_data(as_text=True).startswith("CLASSIC ")

            if not anonymous_browsing and not authenticated:
                assert displayed_flashes == [
                    ("message", "Please log in to access this page.")]
    finally:
        monkey.undo()


@pytest.mark.unit
def test_successful_login_clears_classic_then_no_js_bridge_terminates(tmp_path):
    """Pin the login-required no-JS state machine through a successful login.

    Login clears the Classic flag, so `/` first returns to `/app`; the shell's
    no-JS fallback then marks the session again and terminates on Classic. This
    is one extra capability-detection pass, never a redirect loop.
    """
    from cps.cw_login import login_user
    from cps import ub

    app, web_mod, monkey = _no_js_bridge_app(tmp_path, authenticated=True)
    login_user_row = MagicMock()
    login_user_row.is_active = True
    login_user_row.get_id.return_value = "7"

    @app.route("/test-successful-login")
    def _test_successful_login():
        assert login_user(login_user_row)
        return flask.redirect("/")

    try:
        with patch.object(web_mod.config, "config_anonbrowse", 0, create=True), \
             patch.object(web_mod.config, "config_login_type", 0, create=True), \
             patch.object(web_mod.config,
                          "config_allow_reverse_proxy_header_login", False,
                          create=True), \
             patch.object(web_mod.config, "config_disable_standard_login",
                          False, create=True), \
             patch.object(web_mod.config, "config_enable_oauth_auto_forward",
                          False, create=True), \
             patch.object(web_mod, "render_books_list",
                          return_value="CLASSIC HOME"), \
             patch.object(web_mod, "render_login",
                          return_value="CLASSIC LOGIN"):
            client = app.test_client()
            classic_login, first_chain = _walk_no_js_bridge(client)
            assert classic_login.get_data(as_text=True) == "CLASSIC LOGIN"
            assert first_chain[-1].startswith("/login?")
            with client.session_transaction() as sess:
                assert sess[spa_mod.CLASSIC_SESSION_KEY] is True

            # This minimal app has no configured user-session database. Keep the
            # real login signal fan-out but make its unrelated DB receiver see
            # the just-created session as already stored.
            with patch.object(ub, "check_user_session", return_value=True):
                logged_in = client.get("/test-successful-login")
            assert logged_in.status_code == 302
            assert logged_in.headers["Location"] == "/"
            with client.session_transaction() as sess:
                assert spa_mod.CLASSIC_SESSION_KEY not in sess

            classic_home, post_login_chain = _walk_no_js_bridge(
                client, start=logged_in.headers["Location"])
            assert classic_home.get_data(as_text=True) == "CLASSIC HOME"
            assert post_login_chain == [
                "/", "/app/", "/?cwng_feedback=newui"]
    finally:
        monkey.undo()


@pytest.mark.unit
def test_logout_clears_classic_session_before_next_login(tmp_path):
    from cps.cw_login import LoginManager
    from cps.logout import cleanup_local_logout

    app, monkey = _sticky_app(tmp_path)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def _load_user(_user_id, *_session_parts):
        return None

    @app.route("/test-logout")
    def _test_logout():
        cleanup_local_logout()
        return "LOGGED OUT"

    try:
        client = app.test_client()
        with client.session_transaction() as sess:
            sess[spa_mod.CLASSIC_SESSION_KEY] = True
        assert client.get("/test-logout").status_code == 200
        with client.session_transaction() as sess:
            assert spa_mod.CLASSIC_SESSION_KEY not in sess

        next_login = client.get("/", headers=_HTML_ACCEPT)
        assert next_login.status_code == 302
        assert next_login.headers["Location"] == "/app/"
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
def test_web_index_wires_session_helpers():
    """web.py:index must mark cwng_feedback and call the redirect helper —
    pins that the stand-in '/' route above mirrors production."""
    src = _WEB.read_text()
    assert "spa.prefer_classic_for_session" in src
    assert "spa.stamp_prefer_classic_cookie" not in src
    assert "spa.clear_prefer_spa_cookie" in src
    assert "spa.classic_index_redirects_to_spa" in src
    assert "cwng_feedback" in src
