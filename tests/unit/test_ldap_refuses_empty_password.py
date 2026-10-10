# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""An empty password never reaches an LDAP bind.

The stand-in directory accepts every bind for a user it knows, so these tests
observe the sign-in code's own check rather than the directory's.
"""

from unittest.mock import MagicMock, patch

import flask
import pytest

from cps import constants, usermanagement
from cps.services import simpleldap


pytestmark = pytest.mark.unit


class _DirectoryAcceptingUnauthenticatedBinds:
    def __init__(self):
        self.binds = []

    def get_object_details(self, user=None, **_):
        return {"uid": [user]}

    def bind_user(self, username, password):
        self.binds.append((username, password))
        return True


@pytest.mark.parametrize("password", ["", None])
def test_bind_refuses_an_empty_password_without_asking_the_directory(password):
    directory = _DirectoryAcceptingUnauthenticatedBinds()
    with patch.object(simpleldap, "_ldap", directory):
        assert simpleldap.bind_user("alice", password) == (False, None)
    assert directory.binds == []


def test_bind_passes_a_whitespace_password_to_the_directory():
    # A space is a real password; the directory decides whether it is right.
    directory = _DirectoryAcceptingUnauthenticatedBinds()
    with patch.object(simpleldap, "_ldap", directory):
        assert simpleldap.bind_user("alice", " ") == (True, None)
    assert directory.binds == [("alice", " ")]


def _basic_auth(username, password, *, existing_user):
    user = None
    if existing_user:
        user = usermanagement.ub.User()
        user.id = 7
        user.name = username
        user.password = "unused"
    session = MagicMock()
    session.query.return_value.filter.return_value.first.return_value = user
    directory = _DirectoryAcceptingUnauthenticatedBinds()
    create_user = MagicMock(return_value=(True, None))
    app = flask.Flask(__name__)
    with app.test_request_context("/opds"), \
            patch.object(simpleldap, "_ldap", directory), \
            patch.object(usermanagement.services, "ldap", simpleldap), \
            patch.object(usermanagement.ub, "session", session), \
            patch.object(usermanagement, "limiter", MagicMock()), \
            patch.object(usermanagement, "_verify_app_password_digest", return_value=False), \
            patch.object(usermanagement, "_verify_app_password_older", return_value=False), \
            patch.object(usermanagement.config, "config_login_type", constants.LOGIN_LDAP, create=True), \
            patch.object(usermanagement.config, "config_ldap_auto_create_users", True, create=True), \
            patch("cps.admin.ldap_import_create_user", create_user):
        signed_in = usermanagement.verify_password(username, password)
    return signed_in, directory, create_user


def test_basic_auth_refuses_an_existing_account_with_an_empty_password():
    signed_in, directory, _ = _basic_auth("alice", "", existing_user=True)
    assert signed_in is None
    assert directory.binds == []


def test_basic_auth_creates_no_account_from_an_empty_password():
    signed_in, directory, create_user = _basic_auth("newcomer", "", existing_user=False)
    assert signed_in is None
    assert directory.binds == []
    create_user.assert_not_called()


def test_basic_auth_still_signs_in_with_a_password():
    signed_in, directory, _ = _basic_auth("alice", "secret", existing_user=True)
    assert signed_in is not None and signed_in.name == "alice"
    assert directory.binds == [("alice", "secret")]
