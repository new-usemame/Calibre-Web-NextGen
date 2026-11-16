# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""An account change made outside this process is seen on the next request.

app.db's session lives for the whole process, so a User a request loaded stays in its identity map.
Without a re-read, a password set or an account disabled by another process (an admin script, a
second worker, sqlite3 on the command line) was not seen: the old password kept working, the new
one was refused, and a disabled reader could still download over OPDS. The "other process" here is a
second sqlite3 connection to the same file.
"""

import sqlite3
from types import SimpleNamespace

import pytest
from flask import request
from sqlalchemy import create_engine, event
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash

from cps import constants, ub, usermanagement
from tests.unit.account_reread_world import app, basic_auth, opds_download


pytestmark = pytest.mark.unit


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    path = tmp_path / "app.db"
    engine = create_engine("sqlite:///%s" % path)
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", session)
    user = ub.User()
    user.name = "reader@example.com"
    user.email = "reader@example.com"
    user.role = constants.ROLE_DOWNLOAD | constants.ROLE_PASSWD
    user.password = generate_password_hash("first-password")
    session.add(user)
    session.commit()
    yield SimpleNamespace(path=str(path), session=session, user_id=user.id)
    session.close()
    engine.dispose()


def _change_outside(path, sql, *args):
    con = sqlite3.connect(path)
    con.execute(sql, args)
    con.commit()
    con.close()


def test_a_password_set_by_another_process_is_used_on_the_next_sign_in(app_db):
    assert basic_auth("reader@example.com", "first-password") is not None    # loads and keeps the User
    _change_outside(app_db.path, "update user set password=? where id=?",
                    generate_password_hash("second-password"), app_db.user_id)
    assert basic_auth("reader@example.com", "second-password") is not None
    assert basic_auth("reader@example.com", "first-password") is None


def test_a_role_removed_by_another_process_is_seen_by_the_next_request(app_db):
    user = usermanagement.load_user(app_db.user_id, None, None)
    assert user.role_download()
    _change_outside(app_db.path, "update user set role=0 where id=?", app_db.user_id)
    assert not usermanagement.load_user(app_db.user_id, None, None).role_download()


def test_an_account_deleted_by_another_process_is_gone_on_the_next_request(app_db):
    assert usermanagement.load_user(app_db.user_id, None, None) is not None
    _change_outside(app_db.path, "delete from user where id=?", app_db.user_id)
    assert usermanagement.load_user(app_db.user_id, None, None) is None
    assert basic_auth("reader@example.com", "first-password") is None


def test_an_account_deleted_between_lookup_and_reread_is_not_returned(app_db):
    """A deletion after the lookup must deny access, not return an expired User."""
    user = usermanagement.load_user(app_db.user_id, None, None)
    _change_outside(app_db.path, "delete from user where id=?", app_db.user_id)
    assert usermanagement._fresh_user(user) is None


def test_a_failed_reread_never_returns_the_cached_account(app_db):
    """Only stand-ins bypass re-reading; a real session failure must surface."""
    user = usermanagement.load_user(app_db.user_id, None, None)

    def broken_select(*args, **kwargs):
        raise InvalidRequestError("session cannot refresh")

    event.listen(app_db.session.bind, "before_cursor_execute", broken_select)
    try:
        with pytest.raises(InvalidRequestError, match="session cannot refresh"):
            usermanagement._fresh_user(user)
    finally:
        event.remove(app_db.session.bind, "before_cursor_execute", broken_select)


@pytest.mark.parametrize("kind", ["unmapped", "transient", "detached"])
def test_an_unattached_test_user_is_returned_as_is(app_db, kind):
    """Keep existing auth tests' stand-ins usable without swallowing DB errors."""
    if kind == "unmapped":
        user = SimpleNamespace(id=app_db.user_id)
    elif kind == "transient":
        user = ub.User(name="stand-in")
    else:
        user = usermanagement.load_user(app_db.user_id, None, None)
        app_db.session.expunge(user)
    assert usermanagement._fresh_user(user) is user


def test_proxy_login_uses_current_roles_and_restrictions(app_db, monkeypatch):
    monkeypatch.setattr(usermanagement.config, "config_reverse_proxy_login_header_name",
                        "Remote-User", raising=False)
    with app.test_request_context(headers={"Remote-User": " READER@example.com "}):
        user = usermanagement.load_user_from_reverse_proxy_header(request)
        assert user.role_download()
        _change_outside(app_db.path, "update user set role=0, denied_tags=? where id=?",
                        "adult", app_db.user_id)
        user = usermanagement.load_user_from_reverse_proxy_header(request)
        assert not user.role_download()
        assert user.denied_tags == "adult"


def test_session_ownership_is_checked_after_the_account_reread(app_db):
    """Refreshing an account must not accept another reader's session token."""
    app_db.session.add(ub.User_Sessions(app_db.user_id, "browser", "nonce", 0))
    app_db.session.add(ub.User_Sessions(app_db.user_id + 1, "other", "other-nonce", 0))
    app_db.session.commit()
    user = usermanagement.load_user(app_db.user_id, "nonce", "browser")
    assert user.role_download()
    _change_outside(app_db.path, "update user set role=0 where id=?", app_db.user_id)
    assert not usermanagement.load_user(app_db.user_id, "nonce", "browser").role_download()
    assert usermanagement.load_user(app_db.user_id, "other-nonce", "other") is None
    assert usermanagement.load_user(app_db.user_id, "other-nonce", None) is None
    assert usermanagement.load_user("invalid", "nonce", "browser") is None


def test_opds_download_checks_the_current_password_and_role(app_db):
    """Drive the real OPDS download view and its Basic-auth/permission gates."""
    assert opds_download("reader@example.com", "first-password").status_code == 200
    _change_outside(app_db.path, "update user set password=? where id=?",
                    generate_password_hash("second-password"), app_db.user_id)
    assert opds_download("reader@example.com", "first-password").status_code == 401
    assert opds_download("reader@example.com", "second-password").status_code == 200
    _change_outside(app_db.path, "update user set role=0 where id=?", app_db.user_id)
    assert opds_download("reader@example.com", "second-password").status_code == 401
