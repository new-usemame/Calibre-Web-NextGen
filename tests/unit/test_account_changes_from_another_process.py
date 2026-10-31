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
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash

from cps import constants, ub, usermanagement
from tests.unit.account_reread_world import basic_auth


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
