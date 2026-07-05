import inspect
from datetime import datetime
import pytest
import flask
from unittest.mock import patch, MagicMock

import cps.api.auth


def _app():
    from cps.api import api_v1
    app = flask.Flask(__name__)
    app.testing = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SECRET_KEY"] = "test"
    app.config["RATELIMIT_ENABLED"] = False  # disable rate-limiting in unit tests
    app.register_blueprint(api_v1)
    return app


@pytest.mark.unit
def test_csrf_returns_token_key():
    resp = _app().test_client().get("/api/v1/auth/csrf")
    assert resp.status_code == 200
    assert "csrf_token" in resp.get_json()


@pytest.mark.unit
def test_me_anonymous_401():
    app = _app()
    with patch("cps.api.auth.current_user") as cu:
        cu.is_authenticated = False
        resp = app.test_client().get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.unit
def test_me_authenticated_returns_user():
    app = _app()
    from cps import ub, constants
    u = ub.User()
    u.id, u.name, u.locale, u.theme = 5, "maggie", "en", 1
    u.role = constants.ROLE_USER
    with patch("cps.api.auth.current_user", u):
        resp = app.test_client().get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.get_json()["name"] == "maggie"


@pytest.mark.unit
def test_login_success():
    app = _app()
    from cps import ub, constants
    u = ub.User()
    u.id, u.name, u.password, u.locale, u.theme = 1, "admin", "hash", "en", 1
    u.role = constants.ROLE_ADMIN
    mock_session = MagicMock()
    mock_session.query.return_value.filter.return_value.first.return_value = u
    with patch("cps.api.auth.ub.session", mock_session), \
         patch("cps.api.auth.check_password_hash", return_value=True), \
         patch("cps.api.auth.config.config_disable_standard_login", False, create=True), \
         patch("cps.api.auth.login_user") as lu:
        resp = app.test_client().post("/api/v1/auth/login", json={"username": "admin", "password": "x"})
    assert resp.status_code == 200
    # M3: assert login_user called with the exact user object and remember=False
    lu.assert_called_once_with(u, remember=False)
    assert resp.get_json()["name"] == "admin"


@pytest.mark.unit
def test_login_bad_password_401():
    app = _app()
    from cps import ub, constants
    u = ub.User()
    u.name, u.password = "admin", "hash"
    u.role = constants.ROLE_ADMIN
    mock_session = MagicMock()
    mock_session.query.return_value.filter.return_value.first.return_value = u
    with patch("cps.api.auth.ub.session", mock_session), \
         patch("cps.api.auth.check_password_hash", return_value=False), \
         patch("cps.api.auth.config.config_disable_standard_login", False, create=True), \
         patch("cps.api.auth.login_user") as lu:
        resp = app.test_client().post("/api/v1/auth/login", json={"username": "admin", "password": "x"})
    assert resp.status_code == 401
    assert not lu.called
    # M3: assert the response body carries the expected error code
    body = resp.get_json()
    assert body["error"]["code"] == "invalid_credentials"


@pytest.mark.unit
def test_logout_204():
    app = _app()
    with patch("cps.api.auth.logout_user") as lo:
        resp = app.test_client().post("/api/v1/auth/logout")
    assert resp.status_code == 204
    assert lo.called


# ── Regression: I1 — rate-limit decorator is present on auth_login ────────────

@pytest.mark.unit
def test_auth_login_has_rate_limit_decorator():
    """Source-pin: auth_login must carry flask_limiter rate-limit decorators.

    We inspect the source of the module-level function (before Flask unwraps it)
    to confirm both limit strings are present.  This will fail if the @limiter.limit
    decorators are removed.
    """
    src = inspect.getsource(cps.api.auth.auth_login)
    # The decorator stacks are on auth_login's own source lines.
    # Since limiter.limit wraps it, getsource returns the inner function; check the module.
    module_src = inspect.getsource(cps.api.auth)
    assert "40/day" in module_src, "40/day rate limit missing from cps.api.auth"
    assert "3/minute" in module_src, "3/minute rate limit missing from cps.api.auth"
    assert "_login_key_func" in module_src, "key_func helper missing from cps.api.auth"


# ── Regression: I2 — standard_login_disabled returns 403 ────────────────────

@pytest.mark.unit
def test_login_standard_login_disabled_returns_403():
    """When config_disable_standard_login is True, auth_login must return 403
    with code='standard_login_disabled' and must NOT call login_user."""
    app = _app()
    with patch("cps.api.auth.config.config_disable_standard_login", True, create=True), \
         patch("cps.api.auth.login_user") as lu:
        resp = app.test_client().post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "x"},
        )
    assert resp.status_code == 403
    body = resp.get_json()
    assert body["error"]["code"] == "standard_login_disabled"
    lu.assert_not_called()


# ── register / forgot / config (#22) ─────────────────────────────────────────

@pytest.mark.unit
def test_auth_config_is_public_and_shaped():
    app = _app()
    with patch.object(cps.api.auth, "config") as cfg, \
         patch.object(cps.api.auth, "_oauth_providers", return_value=[]):
        cfg.config_public_reg = True
        cfg.config_register_email = False
        cfg.get_mail_server_configured.return_value = True
        cfg.config_disable_standard_login = False
        cfg.config_calibre_web_title = "Calibre-Web NextGen"
        resp = app.test_client().get("/api/v1/auth/config")
    assert resp.status_code == 200
    d = resp.get_json()
    assert d["public_registration"] is True
    assert d["mail_configured"] is True
    assert d["oauth_providers"] == []
    assert d["instance_name"] == "Calibre-Web NextGen"


@pytest.mark.unit
def test_register_disabled_returns_403():
    app = _app()
    with patch.object(cps.api.auth, "config") as cfg:
        cfg.config_public_reg = False
        resp = app.test_client().post("/api/v1/auth/register",
                                      json={"name": "x", "email": "y@z.com"})
    assert resp.status_code == 403
    assert resp.get_json()["error"]["code"] == "registration_disabled"


@pytest.mark.unit
def test_register_requires_mail_configured():
    app = _app()
    with patch.object(cps.api.auth, "config") as cfg:
        cfg.config_public_reg = True
        cfg.get_mail_server_configured.return_value = False
        resp = app.test_client().post("/api/v1/auth/register",
                                      json={"name": "x", "email": "y@z.com"})
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "mail_not_configured"


@pytest.mark.unit
def test_forgot_always_ok_even_for_unknown_user():
    app = _app()
    with patch.object(cps.api.auth, "ub") as ub:
        ub.session.query.return_value.filter.return_value.first.return_value = None
        resp = app.test_client().post("/api/v1/auth/forgot", json={"username": "ghost"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


@pytest.mark.unit
def test_oauth_providers_hidden_unless_login_type_oauth():
    """REGRESSION: OAuth buttons must only appear when the instance login type is
    OAuth — matching the classic login page. Otherwise they'd show + error on a
    standard/LDAP-login instance where OAuth isn't configured."""
    from cps import constants
    with patch.object(cps.api.auth, "config") as cfg, \
         patch.dict("cps.oauth_bb.oauth_check", {1: "GitHub", 3: "Generic"}, clear=True):
        cfg.config_login_type = constants.LOGIN_STANDARD
        assert cps.api.auth._oauth_providers() == []


@pytest.mark.unit
def test_oauth_providers_maps_ids_to_urls_when_oauth():
    from cps import constants
    with patch.object(cps.api.auth, "config") as cfg, \
         patch.object(cps.api.auth, "url_for", side_effect=lambda ep: "/" + ep.replace(".", "/")), \
         patch.dict("cps.oauth_bb.oauth_check", {1: "GitHub", 2: "Google"}, clear=True):
        cfg.config_login_type = constants.LOGIN_OAUTH
        provs = cps.api.auth._oauth_providers()
    by_id = {p["id"]: p for p in provs}
    assert by_id[1]["url"] == "/oauth/github_login"
    assert by_id[2]["name"] == "Google"


# ── Magic-link (remote) login ────────────────────────────────────────────────

@pytest.mark.unit
def test_magic_link_start_disabled_returns_403():
    app = _app()
    with patch.object(cps.api.auth, "config") as cfg:
        cfg.config_remote_login = False
        resp = app.test_client().post("/api/v1/auth/magic-link/start")
    assert resp.status_code == 403
    assert resp.get_json()["error"]["code"] == "magic_link_disabled"


@pytest.mark.unit
def test_magic_link_start_mints_token():
    app = _app()
    fake_token = MagicMock()
    fake_token.auth_token = "abc123"
    anon = MagicMock()
    anon.is_authenticated = False
    with patch.object(cps.api.auth, "config") as cfg, \
         patch.object(cps.api.auth, "ub") as ub, \
         patch.object(cps.api.auth, "current_user", anon), \
         patch.object(cps.api.auth, "url_for", return_value="http://x/verify/abc123"), \
         patch.object(cps.api.auth, "_build_qr_data_url", return_value="data:image/jpeg;base64,QR"):
        cfg.config_remote_login = True
        ub.RemoteAuthToken.return_value = fake_token
        d = app.test_client().post("/api/v1/auth/magic-link/start").get_json()
    assert d["token"] == "abc123"
    assert d["verify_url"] == "http://x/verify/abc123"
    assert d["qrcode"] == "data:image/jpeg;base64,QR"
    assert d["expires_in_minutes"] == 10


@pytest.mark.unit
def test_magic_link_poll_not_found_for_unknown_token():
    app = _app()
    with patch.object(cps.api.auth, "config") as cfg, \
         patch.object(cps.api.auth, "ub") as ub:
        cfg.config_remote_login = True
        ub.session.query.return_value.filter.return_value.first.return_value = None
        d = app.test_client().post("/api/v1/auth/magic-link/poll",
                                   json={"token": "nope"}).get_json()
    assert d["status"] == "not_found"


@pytest.mark.unit
def test_magic_link_poll_not_verified():
    app = _app()
    tok = MagicMock()
    tok.verified = False
    tok.expiration = datetime(2999, 1, 1)
    with patch.object(cps.api.auth, "config") as cfg, \
         patch.object(cps.api.auth, "ub") as ub:
        cfg.config_remote_login = True
        ub.session.query.return_value.filter.return_value.first.return_value = tok
        d = app.test_client().post("/api/v1/auth/magic-link/poll",
                                   json={"token": "t"}).get_json()
    assert d["status"] == "not_verified"


@pytest.mark.unit
def test_magic_link_poll_success_logs_in_and_consumes_token():
    """A verified token logs the waiting device in, returns the serialized user,
    and the token is deleted (consumed)."""
    app = _app()
    from cps import constants
    tok = MagicMock()
    tok.verified = True
    tok.expiration = datetime(2999, 1, 1)
    tok.user_id = 7
    user = MagicMock()
    user.id, user.name, user.locale, user.theme = 7, "maggie", "en", 1
    user.role = constants.ROLE_USER
    user.role_anonymous.return_value = False
    with patch.object(cps.api.auth, "config") as cfg, \
         patch.object(cps.api.auth, "ub") as ub, \
         patch.object(cps.api.auth, "login_user") as lu, \
         patch.object(cps.api.auth, "serialize_user", return_value={"name": "maggie"}), \
         patch.object(cps.api.auth, "_server_features", return_value={}), \
         patch.object(cps.api.auth, "_user_avatar", return_value=None):
        cfg.config_remote_login = True
        cfg.config_calibre_web_title = "Calibre-Web NextGen"
        # token lookup, then user lookup
        q = ub.session.query.return_value.filter.return_value
        q.first.side_effect = [tok, user]
        d = app.test_client().post("/api/v1/auth/magic-link/poll",
                                   json={"token": "t"}).get_json()
    assert d["status"] == "success"
    assert d["user"]["name"] == "maggie"
    # #668: magic-link now returns the same me-shape as /me and login (built by
    # the shared _me_payload), so instance_name + avatar are present.
    assert d["user"]["instance_name"] == "Calibre-Web NextGen"
    assert d["user"]["avatar"] is None
    lu.assert_called_once_with(user)
    ub.session.delete.assert_called_once_with(tok)


@pytest.mark.unit
def test_magic_link_endpoints_are_public():
    """The before_request gate must let the magic-link endpoints through while
    logged out — they're for the unauthenticated device by definition."""
    from cps.api import _PUBLIC_ENDPOINTS
    assert "api_v1.auth_magic_link_start" in _PUBLIC_ENDPOINTS
    assert "api_v1.auth_magic_link_poll" in _PUBLIC_ENDPOINTS


@pytest.mark.unit
def test_auth_config_exposes_remote_login():
    """Magic-link (remote) login is surfaced for the SPA login when enabled."""
    app = _app()
    with patch.object(cps.api.auth, "config") as cfg, \
         patch.object(cps.api.auth, "_oauth_providers", return_value=[]), \
         patch.object(cps.api.auth, "url_for", return_value="/remote/login"):
        cfg.config_public_reg = False
        cfg.config_register_email = False
        cfg.get_mail_server_configured.return_value = True
        cfg.config_disable_standard_login = False
        cfg.config_remote_login = True
        cfg.config_calibre_web_title = "Calibre-Web NextGen"
        d = app.test_client().get("/api/v1/auth/config").get_json()
    assert d["remote_login"] is True
    assert d["remote_login_url"] == "/remote/login"
