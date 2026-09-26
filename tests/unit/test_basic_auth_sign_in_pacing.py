# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""HTTP basic sign-in paces password guesses the same way for every login type.

A real limiter runs, and each request goes through the same
``verify_password`` a catalogue client reaches. What is counted is a client's
new wrong passwords for an account (``rate_limits.BasicAuthPacing``).
"""

from unittest.mock import MagicMock, patch

import flask
import pytest
from flask_limiter import Limiter
from flask_simpleldap import LDAPException

from cps import constants, usermanagement
from cps.services import simpleldap


pytestmark = pytest.mark.unit

ATTEMPTS_PER_MINUTE = 3
GUESSES = ["guess-%d" % n for n in range(ATTEMPTS_PER_MINUTE + 2)]


class _Directory:
    """Knows one person, whose password is ``right``."""

    down = False

    def get_object_details(self, user=None, **_):
        if self.down:
            raise LDAPException("Can't contact LDAP server")
        return {"uid": [user]}

    binds = 0

    def bind_user(self, username, password):
        type(self).binds += 1
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

    app.register_blueprint(catalogue)
    limiter.init_app(app)

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
    statuses = _statuses(login_type, GUESSES + ["right"])
    assert statuses == [401] * ATTEMPTS_PER_MINUTE + [429, 429, 429]


@pytest.mark.parametrize("login_type", [constants.LOGIN_STANDARD, constants.LOGIN_LDAP],
                         ids=["local", "directory"])
def test_a_reader_app_stuck_on_an_old_password_is_not_a_guesser(login_type):
    # It sends the same wrong password on every refresh; the owner's right
    # password, from the same address, still signs in.
    statuses = _statuses(login_type, ["old"] * 10 + ["right"])
    assert statuses == [401] * 10 + [200]


def test_a_device_with_an_app_password_is_never_refused():
    app, patches = _catalogue(constants.LOGIN_STANDARD, existing_user=True)
    patches = [p for p in patches if getattr(p, "attribute", "") != "_verify_app_password_digest"]
    patches.append(patch.object(usermanagement, "_verify_app_password_digest",
                                side_effect=lambda _user, password: password == "device-token"))
    for p in patches:
        p.start()
    try:
        client = app.test_client()
        statuses = [client.get("/catalogue", auth=("alice", pw)).status_code
                    for pw in GUESSES + ["device-token"] * 3]
    finally:
        for p in reversed(patches):
            p.stop()
    assert statuses == [401] * ATTEMPTS_PER_MINUTE + [429, 429] + [200] * 3


def test_a_directory_account_not_yet_imported_is_paced_too():
    statuses = _statuses(constants.LOGIN_LDAP, GUESSES[:ATTEMPTS_PER_MINUTE + 1],
                         existing_user=False, username="newcomer")
    assert statuses == [401] * ATTEMPTS_PER_MINUTE + [429]


@pytest.mark.parametrize("login_type", [constants.LOGIN_STANDARD, constants.LOGIN_LDAP],
                         ids=["local", "directory"])
def test_a_successful_sign_in_resets_the_pace(login_type):
    # A client that signs in on every request must never be slowed by it.
    statuses = _statuses(login_type, GUESSES[:2] + ["right"] + GUESSES[2:4] + ["right"] * 5)
    assert statuses == [401, 401, 200, 401, 401] + [200] * 5


@pytest.mark.parametrize("login_type", [constants.LOGIN_STANDARD, constants.LOGIN_LDAP],
                         ids=["local", "directory"])
def test_a_broken_limiter_store_does_not_lock_clients_out(login_type):
    # An external store (Redis, Memcached) can be down; sign-in still decides
    # on the password alone, as the API sign-in does.
    app, patches = _catalogue(login_type, existing_user=True)
    limiter = patches[0].new
    patches += [patch.object(limiter.limiter, method, side_effect=ConnectionError("store down"))
                for method in ("test", "hit", "clear", "get_window_stats")]
    for p in patches:
        p.start()
    try:
        client = app.test_client()
        statuses = [client.get("/catalogue", auth=("alice", pw)).status_code
                    for pw in ["right"] + GUESSES + ["right"]]
    finally:
        for p in reversed(patches):
            p.stop()
    assert statuses == [200] + [401] * len(GUESSES) + [200]


def test_a_sign_in_succeeds_when_its_pace_cannot_be_cleared():
    app, patches = _catalogue(constants.LOGIN_LDAP, existing_user=True)
    limiter = patches[0].new
    patches.append(patch.object(limiter.limiter.storage, "clear",
                                side_effect=ConnectionError("store down")))
    for p in patches:
        p.start()
    try:
        client = app.test_client()
        statuses = [client.get("/catalogue", auth=("alice", pw)).status_code
                    for pw in ["wrong", "right"]]
    finally:
        for p in reversed(patches):
            p.stop()
    assert statuses == [401, 200]


def test_a_directory_outage_is_not_remembered_as_wrong_passwords():
    """The right password works as soon as the directory is back."""
    app, patches = _catalogue(constants.LOGIN_LDAP, existing_user=True)
    directory = next(p.new for p in patches if isinstance(p.new, _Directory))
    for p in patches:
        p.start()
    try:
        client = app.test_client()
        directory.down = True
        during = [client.get("/catalogue", auth=("alice", "right")).status_code for _ in range(5)]
        directory.down = False
        after = client.get("/catalogue", auth=("alice", "right")).status_code
    finally:
        for p in reversed(patches):
            p.stop()
    assert (during, after) == ([401] * 5, 200)



def test_an_outage_is_not_counted_against_an_account_not_yet_imported():
    """Nobody is paced for sign-ins the directory never answered."""
    app, patches = _catalogue(constants.LOGIN_LDAP, existing_user=False)
    directory = next(p.new for p in patches if isinstance(p.new, _Directory))
    for p in patches:
        p.start()
    try:
        client = app.test_client()
        directory.down = True
        during = [client.get("/catalogue", auth=("alice", g)).status_code for g in GUESSES]
        directory.down = False
        after = [client.get("/catalogue", auth=("alice", g)).status_code for g in GUESSES]
    finally:
        for p in reversed(patches):
            p.stop()
    assert during == [401] * len(GUESSES)
    assert after == [401] * ATTEMPTS_PER_MINUTE + [429, 429]


def test_a_stale_catalogue_app_costs_the_directory_one_bind_a_minute():
    # A failed bind is one a directory may count towards locking the account.
    _Directory.binds = 0
    assert _statuses(constants.LOGIN_LDAP, ["old-password"] * 6) == [401] * 6
    assert _Directory.binds == 1
