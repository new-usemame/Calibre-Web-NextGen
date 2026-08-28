# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flask
import pytest
from werkzeug.security import generate_password_hash

import cps.web


pytestmark = pytest.mark.unit


class _Anonymous:
    is_authenticated = False


def _translate(message, **values):
    return message % values if values else message


def _app():
    app = flask.Flask(__name__)
    app.testing = True
    app.config["SECRET_KEY"] = "test"
    app.config["RATELIMIT_ENABLED"] = False
    app.register_blueprint(cps.web.web)
    return app


def _post_ldap_login(
    *, stored_password, submitted_password, ldap_result, user_exists=True,
    user_name="local-user",
):
    user = None
    if user_exists:
        user = cps.web.ub.User()
        user.id = 1903
        user.name = user_name
        user.password = stored_password
        user.role = cps.web.constants.ROLE_USER

    db_session = MagicMock()
    db_session.query.return_value.filter.return_value.first.return_value = user

    ldap = MagicMock()
    ldap.bind_user.return_value = (ldap_result, "LDAP unavailable")

    activity_log = MagicMock()
    cwa_db_loader = MagicMock(
        return_value=SimpleNamespace(CWA_DB=MagicMock(return_value=activity_log))
    )

    app = _app()
    with app.test_client() as client, \
            patch("cps.web.current_user", _Anonymous()), \
            patch("cps.web.config.config_disable_standard_login", False, create=True), \
            patch("cps.web.config.config_login_type", cps.web.constants.LOGIN_LDAP, create=True), \
            patch("cps.web.config.config_ldap_auto_create_users", False, create=True), \
            patch("cps.web.services.ldap", ldap), \
            patch("cps.web.ub.session", db_session), \
            patch("cps.web.limiter.check") as limiter_check, \
            patch("cps.web.render_login", return_value="login"), \
            patch("cps.web.handle_login_user", return_value="logged-in") as handle_login_user, \
            patch("cps.web._", side_effect=_translate), \
            patch("cps.cwa_db_loader.load_cwa_db", cwa_db_loader):
        response = client.post(
            "/login",
            data={"username": user_name, "password": submitted_password},
        )
        flashes = list(flask.session.get("_flashes", ()))

    return SimpleNamespace(
        response=response,
        user=user,
        ldap=ldap,
        limiter_check=limiter_check,
        handle_login_user=handle_login_user,
        activity_log=activity_log,
        flashes=flashes,
    )


def _wrong_password_flashes(result):
    return [
        message
        for category, message in result.flashes
        if category == "error" and message == "Wrong Username or Password"
    ]


def test_ldap_rejection_accepts_valid_stored_local_password():
    result = _post_ldap_login(
        stored_password=generate_password_hash("local-secret"),
        submitted_password="local-secret",
        ldap_result=False,
    )

    assert result.response.get_data(as_text=True) == "logged-in"
    result.handle_login_user.assert_called_once()
    logged_in_user, remember_me, message, category = result.handle_login_user.call_args.args
    assert logged_in_user is result.user
    assert remember_me is False
    assert "local-user" in message
    assert "LDAP authentication rejected" in message
    assert category == "warning"
    result.activity_log.log_activity.assert_not_called()
    result.limiter_check.assert_called_once_with()


def test_ldap_rejection_refuses_empty_stored_password():
    result = _post_ldap_login(
        stored_password="",
        submitted_password="anything",
        ldap_result=False,
    )

    result.handle_login_user.assert_not_called()
    assert len(_wrong_password_flashes(result)) == 1
    result.activity_log.log_activity.assert_called_once()
    result.limiter_check.assert_called_once_with()


def test_ldap_rejection_refuses_wrong_local_password_with_one_error_flash():
    result = _post_ldap_login(
        stored_password=generate_password_hash("local-secret"),
        submitted_password="wrong-secret",
        ldap_result=False,
    )

    result.handle_login_user.assert_not_called()
    assert len(_wrong_password_flashes(result)) == 1
    result.activity_log.log_activity.assert_called_once()
    result.limiter_check.assert_called_once_with()


def test_ldap_rejection_still_refuses_guest_with_a_valid_local_hash():
    result = _post_ldap_login(
        stored_password=generate_password_hash("guest-secret"),
        submitted_password="guest-secret",
        ldap_result=False,
        user_name="Guest",
    )

    result.handle_login_user.assert_not_called()
    assert len(_wrong_password_flashes(result)) == 1
    result.activity_log.log_activity.assert_called_once()
    result.limiter_check.assert_called_once_with()


def test_ldap_unreachable_keeps_the_existing_local_fallback():
    result = _post_ldap_login(
        stored_password=generate_password_hash("local-secret"),
        submitted_password="local-secret",
        ldap_result=None,
    )

    result.handle_login_user.assert_called_once()
    logged_in_user, remember_me, message, category = result.handle_login_user.call_args.args
    assert logged_in_user is result.user
    assert remember_me is False
    assert message == (
        "Fallback Login as: 'local-user', "
        "LDAP Server not reachable, or user not known"
    )
    assert category == "warning"
    result.activity_log.log_activity.assert_not_called()
    result.limiter_check.assert_called_once_with()


def test_successful_ldap_bind_without_auto_create_has_no_wrong_password_flash():
    result = _post_ldap_login(
        stored_password=None,
        submitted_password="directory-secret",
        ldap_result=True,
        user_exists=False,
    )

    result.handle_login_user.assert_not_called()
    assert (
        "error",
        "Authentication successful, but no local account found. "
        "Please contact your administrator to create your account.",
    ) in result.flashes
    assert _wrong_password_flashes(result) == []
    result.activity_log.log_activity.assert_not_called()
    result.limiter_check.assert_called_once_with()
