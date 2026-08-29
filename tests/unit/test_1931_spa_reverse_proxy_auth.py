# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for reverse-proxy identity on the two SPA surfaces.

The classic comparator is deliberately part of this test app.  The SPA shell
must remain public so it can render the logged-out login tree, while ``/auth/me``
must retain its JSON 401.  Authentication state, however, must be identical to
``@user_login_required`` for the same configured header and local-user lookup.
"""

from unittest.mock import MagicMock, patch

import flask
import pytest
from werkzeug.middleware.proxy_fix import ProxyFix


pytestmark = pytest.mark.unit

_IDENTITY_HEADER = "Remote-User"
_CLIENT_HOP = "client-hop"
_PROXY_HOP = "proxy-hop"


def _user():
    from cps import constants, ub

    user = ub.User()
    user.id = 41
    user.name = "proxy-reader"
    user.locale = "en"
    user.theme = 1
    user.role = constants.ROLE_USER
    user.view_settings = {}
    return user


def _app(monkeypatch, tmp_path):
    from cps.api import api_v1
    from cps.cw_login import current_user
    from cps.cw_login import LoginManager
    from cps.spa import spa
    from cps.usermanagement import user_login_required
    import cps.spa as spa_mod

    (tmp_path / "index.html").write_text(
        "<!doctype html><title>Calibre-Web NextGen</title><div id=root></div>"
    )
    monkeypatch.setattr(spa_mod, "_SPA_DIR", str(tmp_path))
    monkeypatch.delenv("CWNG_SPA", raising=False)

    app = flask.Flask(__name__)
    app.testing = True
    app.config.update(
        SECRET_KEY="test",
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
    )

    class Anonymous:
        is_authenticated = False

    login_manager = LoginManager()
    login_manager.anonymous_user = Anonymous
    login_manager.login_view = "web.login"

    @login_manager.user_loader
    def load_session_user(_user_id, _random=None, _session_key=None):
        return None

    login_manager.init_app(app)

    classic = flask.Blueprint("web", __name__)

    @classic.get("/login")
    def login():
        return "CLASSIC LOGIN"

    app.register_blueprint(classic)

    @app.get("/classic-protected")
    @user_login_required
    def classic_protected():
        return flask.jsonify({"name": current_user.name})

    @app.after_request
    def expose_request_identity_for_assertion(response):
        user = getattr(flask.g, "flask_httpauth_user", None)
        if user is not None:
            response.headers["X-Test-Authenticated-As"] = user.name
        return response

    app.register_blueprint(spa)
    app.register_blueprint(api_v1)

    # Exercise the same configured X-Forwarded-For trust depth as production.
    # The loader must receive Flask's ProxyFix-corrected request, never parse the
    # raw forwarding chain itself.
    app.wsgi_app = ProxyFix(
        app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
    )
    return app


def _get(client, path):
    return client.get(
        path,
        headers={
            _IDENTITY_HEADER: "proxy-reader",
            "X-Forwarded-For": _CLIENT_HOP,
            "X-Forwarded-Proto": "https",
        },
        environ_overrides={"REMOTE_ADDR": _PROXY_HOP},
    )


def _config_patches(*, enabled):
    import cps.usermanagement as usermanagement

    return (
        patch.object(
            usermanagement.config,
            "config_allow_reverse_proxy_header_login",
            enabled,
            create=True,
        ),
        patch.object(
            usermanagement.config,
            "config_reverse_proxy_login_header_name",
            _IDENTITY_HEADER,
            create=True,
        ),
        patch.object(
            usermanagement.config,
            "config_reverse_proxy_auto_create_users",
            False,
            create=True,
        ),
        patch.object(
            usermanagement.config, "config_anonbrowse", 0, create=True
        ),
    )


@pytest.mark.parametrize(
    "spa_path", ["/app/", "/api/v1/auth/me"], ids=["spa-shell", "auth-me"]
)
def test_trusted_header_identifies_local_user_on_classic_and_spa_surface(
    monkeypatch, tmp_path, spa_path
):
    import cps.api.auth as auth
    import cps.usermanagement as usermanagement

    user = _user()
    seen_remote_addresses = []
    session = MagicMock()

    def query(_model):
        seen_remote_addresses.append(flask.request.remote_addr)
        return session.query.return_value

    session.query.side_effect = query
    session.query.return_value.filter.return_value.first.return_value = user
    fake_limiter = MagicMock()
    fake_limiter.current_limits = []

    app = _app(monkeypatch, tmp_path)
    config_patches = _config_patches(enabled=True)
    with config_patches[0], config_patches[1], config_patches[2], config_patches[3], \
            patch.object(usermanagement.ub, "session", session), \
            patch.object(usermanagement, "limiter", fake_limiter), \
            patch.object(auth, "_me_payload", side_effect=lambda value: {"name": value.name}):
        client = app.test_client()
        classic = _get(client, "/classic-protected")
        spa_response = _get(client, spa_path)

    assert classic.status_code == 200
    assert classic.get_json() == {"name": user.name}
    assert spa_response.status_code == 200
    if spa_path == "/app/":
        assert spa_response.headers["X-Test-Authenticated-As"] == user.name
    else:
        assert spa_response.get_json() == {"name": user.name}
    assert seen_remote_addresses
    assert set(seen_remote_addresses) == {_CLIENT_HOP}


def test_same_header_on_untrusted_path_authenticates_nowhere(monkeypatch, tmp_path):
    """The admin trust switch is the classic path's outer trust boundary.

    Supplying the identity and forwarding headers while that path is disabled
    must not even query for an asserted user on Classic, the SPA shell, or /me.
    """
    import cps.api.auth as auth
    import cps.usermanagement as usermanagement

    session = MagicMock()
    app = _app(monkeypatch, tmp_path)
    config_patches = _config_patches(enabled=False)
    with config_patches[0], config_patches[1], config_patches[2], config_patches[3], \
            patch.object(usermanagement.ub, "session", session), \
            patch.object(auth, "_me_payload") as me_payload:
        client = app.test_client()
        classic = _get(client, "/classic-protected")
        shell = _get(client, "/app/")
        me = _get(client, "/api/v1/auth/me")

    assert classic.status_code == 302
    assert classic.headers["Location"].endswith("/login?next=%2Fclassic-protected")
    assert shell.status_code == 200
    assert "X-Test-Authenticated-As" not in shell.headers
    assert me.status_code == 401
    assert me.get_json()["error"]["code"] == "unauthenticated"
    session.query.assert_not_called()
    me_payload.assert_not_called()


def test_nonexistent_header_user_matches_classic_refusal(monkeypatch, tmp_path):
    """An enabled trusted path does not imply account creation.

    With the existing auto-create setting off, the shared loader returns no user;
    Classic refuses, /me returns JSON 401, and the public shell has no identity.
    """
    import cps.api.auth as auth
    import cps.usermanagement as usermanagement

    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    fake_limiter = MagicMock()
    fake_limiter.current_limits = []

    app = _app(monkeypatch, tmp_path)
    config_patches = _config_patches(enabled=True)
    with config_patches[0], config_patches[1], config_patches[2], config_patches[3], \
            patch.object(usermanagement.ub, "session", session), \
            patch.object(usermanagement, "limiter", fake_limiter), \
            patch.object(usermanagement, "create_authenticated_user") as create_user, \
            patch.object(auth, "_me_payload") as me_payload:
        client = app.test_client()
        classic = _get(client, "/classic-protected")
        shell = _get(client, "/app/")
        me = _get(client, "/api/v1/auth/me")

    assert classic.status_code == 302
    assert classic.headers["Location"].endswith("/login?next=%2Fclassic-protected")
    assert shell.status_code == 200
    assert "X-Test-Authenticated-As" not in shell.headers
    assert me.status_code == 401
    assert me.get_json()["error"]["code"] == "unauthenticated"
    create_user.assert_not_called()
    me_payload.assert_not_called()
