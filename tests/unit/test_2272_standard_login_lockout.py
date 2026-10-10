# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Discussion #2272: turning SSO off must not leave the instance with no way in.

"Disable Standard Login" is a separate setting from the login type. An admin who
switched the login type back from OAuth (or had no provider left to offer) kept
the flag, and every login surface went on honouring it: the classic form was
hidden, the classic POST and the SPA API both refused a password, and the SPA
login page rendered no form. The flag may only withhold the password login while
there is an OAuth provider on the page to sign in with instead.
"""

from unittest.mock import MagicMock, patch

import flask
import pytest

import cps.api.auth
import cps.web
from cps import constants, ub


pytestmark = pytest.mark.unit

_GATE_MESSAGE = "Standard login is disabled."


def _api_app():
    from cps.api import api_v1
    app = flask.Flask(__name__)
    app.testing = True
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["SECRET_KEY"] = "test"
    app.config["RATELIMIT_ENABLED"] = False
    app.register_blueprint(api_v1)
    return app


def _config_state(login_type, providers):
    """The two settings plus the providers actually registered at startup."""
    cfg = cps.api.auth.config  # the one ConfigSQL instance both surfaces read
    return (
        patch.object(cfg, "config_disable_standard_login", True, create=True),
        patch.object(cfg, "config_login_type", login_type, create=True),
        patch.dict("cps.oauth_bb.oauth_check", providers, clear=True),
    )


# (login type, registered providers, password login still offered?)
CASES = [
    pytest.param(constants.LOGIN_STANDARD, {}, True, id="login-type-back-to-standard"),
    pytest.param(constants.LOGIN_STANDARD, {3: "generic"}, True,
                 id="standard-with-stale-provider-row"),
    pytest.param(constants.LOGIN_LDAP, {}, True, id="ldap"),
    pytest.param(constants.LOGIN_OAUTH, {}, True, id="oauth-with-no-provider-left"),
    pytest.param(constants.LOGIN_OAUTH, {3: "generic"}, False, id="oauth-with-provider"),
]


@pytest.mark.parametrize(("login_type", "providers", "offered"), CASES)
def test_spa_login_config_offers_password_form_unless_sso_can_replace_it(
        login_type, providers, offered):
    a, b, c = _config_state(login_type, providers)
    with a, b, c:
        body = _api_app().test_client().get("/api/v1/auth/config").get_json()
    assert body["standard_login_disabled"] is (not offered)


@pytest.mark.parametrize(("login_type", "providers", "offered"), CASES)
def test_spa_password_login_is_refused_only_when_sso_can_replace_it(
        login_type, providers, offered):
    user = ub.User()
    user.id, user.name, user.role, user.password = 1, "admin", constants.ROLE_ADMIN, "h"
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = user
    a, b, c = _config_state(login_type, providers)
    with a, b, c, \
            patch("cps.api.auth.services.ldap", None), \
            patch("cps.api.auth.ub.session", session), \
            patch("cps.api.auth.check_password_hash", return_value=True), \
            patch("cps.api.auth.login_user") as login:
        response = _api_app().test_client().post(
            "/api/v1/auth/login", json={"username": "admin", "password": "pw"})

    if offered:
        assert response.status_code == 200
        login.assert_called_once()
    else:
        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "standard_login_disabled"
        login.assert_not_called()


@pytest.mark.parametrize(("login_type", "providers", "offered"), CASES)
def test_classic_login_post_passes_the_gate_unless_sso_can_replace_it(
        login_type, providers, offered):
    app = flask.Flask(__name__)
    app.testing = True
    app.config["SECRET_KEY"] = "test"
    app.register_blueprint(cps.web.web)
    a, b, c = _config_state(login_type, providers)
    with a, b, c, \
            patch("cps.web.flash") as flash, \
            patch("cps.web._", side_effect=lambda text, **_kw: text), \
            patch("cps.web.render_login", return_value="login page"), \
            patch("cps.web.limiter.check", side_effect=RuntimeError("stop after gate")):
        app.test_client().post("/login", data={"username": "admin", "password": "pw"})

    shown = [call.args[0] for call in flash.call_args_list]
    assert (_GATE_MESSAGE in shown) is (not offered)
