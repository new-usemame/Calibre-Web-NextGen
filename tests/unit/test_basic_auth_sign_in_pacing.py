# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""HTTP basic sign-in paces wrong passwords the same way for every login type.

A real limiter runs with the catalogue's own per-account limit, and each
request goes through the same ``verify_password`` a client reaches.
"""

from unittest.mock import MagicMock, patch

import flask
import pytest
from flask_limiter import Limiter, RateLimitExceeded

from cps import constants, usermanagement
from cps.services import simpleldap


pytestmark = pytest.mark.unit

ATTEMPTS_PER_MINUTE = 3


class _Directory:
    """Knows one person, whose password is ``right``."""

    def get_object_details(self, user=None, **_):
        return {"uid": [user]}

    def bind_user(self, username, password):
        return True if password == "right" else None


def _catalogue(login_type, *, existing_user):
    user = None
    if existing_user:
        user = usermanagement.ub.User()
        user.id = 7
        user.name = "alice"
        user.password = "not-a-hash"
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = user

    app = flask.Flask(__name__)
    limiter = Limiter(key_func=lambda: "unused", auto_check=False, storage_uri="memory://")
    catalogue = flask.Blueprint("catalogue", __name__)

    @catalogue.route("/catalogue")
    def feed():
        auth = flask.request.authorization
        signed_in = usermanagement.verify_password(auth.username, auth.password)
        return ("ok", 200) if signed_in else ("no", 401)

    limiter.limit(f"{ATTEMPTS_PER_MINUTE}/minute",
                  key_func=lambda: flask.request.authorization.username)(catalogue)
    app.register_blueprint(catalogue)
    limiter.init_app(app)

    @app.errorhandler(RateLimitExceeded)
    def _too_many(_):
        return "slow down", 429

    patches = [
        patch.object(usermanagement, "limiter", limiter),
        patch.object(usermanagement.ub, "session", session),
        patch.object(simpleldap, "_ldap", _Directory()),
        patch.object(usermanagement.services, "ldap", simpleldap),
        patch.object(usermanagement, "_verify_app_password_digest", return_value=False),
        patch.object(usermanagement, "_verify_app_password_older", return_value=False),
        patch.object(usermanagement, "check_password_hash",
                     side_effect=lambda _hash, password: password == "right"),
        patch.object(usermanagement.config, "config_login_type", login_type, create=True),
        patch.object(usermanagement.config, "config_ldap_auto_create_users", True, create=True),
        patch("cps.admin.ldap_import_create_user", MagicMock(return_value=(False, "not in test"))),
    ]
    return app, patches


def _statuses(login_type, passwords, *, existing_user=True, username="alice"):
    app, patches = _catalogue(login_type, existing_user=existing_user)
    for p in patches:
        p.start()
    try:
        client = app.test_client()
        return [client.get("/catalogue", auth=(username, pw)).status_code for pw in passwords]
    finally:
        for p in reversed(patches):
            p.stop()


@pytest.mark.parametrize("login_type", [constants.LOGIN_STANDARD, constants.LOGIN_LDAP],
                         ids=["local", "directory"])
def test_wrong_passwords_are_paced_for_every_login_type(login_type):
    statuses = _statuses(login_type, ["wrong"] * (ATTEMPTS_PER_MINUTE + 2))
    assert statuses == [401] * ATTEMPTS_PER_MINUTE + [429, 429]


def test_a_directory_account_not_yet_imported_is_paced_too():
    statuses = _statuses(constants.LOGIN_LDAP, ["wrong"] * (ATTEMPTS_PER_MINUTE + 1),
                         existing_user=False, username="newcomer")
    assert statuses == [401] * ATTEMPTS_PER_MINUTE + [429]


@pytest.mark.parametrize("login_type", [constants.LOGIN_STANDARD, constants.LOGIN_LDAP],
                         ids=["local", "directory"])
def test_a_successful_sign_in_resets_the_pace(login_type):
    # A client that signs in on every request must never be slowed by it.
    wrong = ["wrong"] * (ATTEMPTS_PER_MINUTE - 1)
    statuses = _statuses(login_type, wrong + ["right"] + wrong + ["right"] * 5)
    assert statuses == [401, 401, 200, 401, 401] + [200] * 5
