# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Signing in with an app password must not cost a slow hash per app password.

A KOReader device sends its HTTP Basic credentials with every request: the
library manifest, every placeholder, every book, every progress update. App
passwords were kept only as deliberately slow werkzeug hashes, and sign-in
compared the password with each of the account's app passwords in turn. On the
test rig an account with eleven app passwords took 0.87 s per request against
0.09 s for the account password, so a Kindle fetching fifty placeholders spent
most of a minute signing in.

What is counted is the number of password-hash computations one sign-in
performs: werkzeug's ``_hash_internal``, which every scrypt and pbkdf2 check
goes through. Sign-in goes through the real KOReader sync route and the real
Basic-auth decorator OPDS uses.
"""

import secrets
from datetime import datetime, timedelta, timezone

import pytest
import werkzeug.security
from flask import g
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from werkzeug.security import generate_password_hash

from cps import ub, usermanagement
from cps.services import app_passwords
from tests.unit.koreader_library_world import LibraryWorld

pytestmark = pytest.mark.unit

MANY = 12


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = LibraryWorld(monkeypatch, tmp_path)
    w.enable_web()

    @w.app.route("/test/opds")
    @usermanagement.requires_basic_auth_if_no_ano
    def _opds_like():
        return g.flask_httpauth_user.name

    w.add_user("alice", password="alice-account-password")
    w.add_user("bob", password="bob-account-password")
    yield w
    w.close()


@pytest.fixture
def slow_hashes(monkeypatch):
    """Every password hash werkzeug computes, by method; ``clear()`` to reset."""
    calls = []
    original = werkzeug.security._hash_internal

    def counting(method, salt, password):
        calls.append(method.split(":")[0])
        return original(method, salt, password)

    monkeypatch.setattr(werkzeug.security, "_hash_internal", counting)
    return calls


def account(world, name):
    return world.session.query(ub.User).filter(ub.User.name == name).one()


def mint(world, name, count=1):
    """App passwords made the way the account page and pairing make them."""
    user = account(world, name)
    passwords = []
    for index in range(count):
        _row, cleartext = app_passwords.mint(user.id, "Kindle %d" % index,
                                             session=world.session)
        passwords.append(cleartext)
    world.session.commit()
    return passwords


def older_app_password(world, name, label="Older Kobo"):
    """An app password saved the way releases before this one saved it."""
    cleartext = secrets.token_urlsafe(32)
    row = ub.UserAppPassword(user_id=account(world, name).id, label=label,
                             password_hash=generate_password_hash(cleartext))
    world.session.add(row)
    world.session.commit()
    return row, cleartext


def kosync_sign_in(world, name, password):
    return world.client.get("/kosync/users/auth",
                            headers=world.basic(name, password)).status_code


def opds_sign_in(world, name, password):
    response = world.client.get("/test/opds", headers=world.basic(name, password))
    return response.status_code, response.get_data(as_text=True)


def test_a_device_signs_in_with_its_app_password_without_a_slow_hash(world, slow_hashes):
    passwords = mint(world, "alice", MANY)
    device_password = passwords[MANY // 2]

    slow_hashes.clear()
    assert kosync_sign_in(world, "alice", device_password) == 200
    assert slow_hashes == []

    slow_hashes.clear()
    assert opds_sign_in(world, "alice", device_password) == (200, "alice")
    assert slow_hashes == []

    # The counter counts: the account password costs its own hash, and only
    # that, however many app passwords the account has.
    slow_hashes.clear()
    assert kosync_sign_in(world, "alice", "alice-account-password") == 200
    assert len(slow_hashes) == 1
    slow_hashes.clear()
    assert opds_sign_in(world, "alice", "alice-account-password") == (200, "alice")
    assert len(slow_hashes) == 1


class FakeDirectory:
    """An LDAP server that knows one password per account and logs every bind."""

    def __init__(self, passwords):
        self.passwords = passwords
        self.binds = []

    def bind_user(self, name, password):
        self.binds.append(name)
        if self.passwords.get(name) == password:
            return True, None
        return False, "Invalid credentials"


def test_an_app_password_is_never_tried_on_the_directory(world, monkeypatch):
    """With LDAP sign-in, an app password sent to the directory is a failed
    login there, and directories lock accounts after a few. A device sending
    its app password with every request must not cost the person their
    directory account."""
    from cps import config, constants, services
    directory = FakeDirectory({"alice": "alice-directory-password"})
    monkeypatch.setattr(services, "ldap", directory)
    monkeypatch.setattr(config, "config_login_type", constants.LOGIN_LDAP)
    device_password = mint(world, "alice", 3)[1]

    for _ in range(3):
        assert kosync_sign_in(world, "alice", device_password) == 200
        assert opds_sign_in(world, "alice", device_password) == (200, "alice")
    assert directory.binds == []

    # The directory password still goes to the directory.
    assert kosync_sign_in(world, "alice", "alice-directory-password") == 200
    assert opds_sign_in(world, "alice", "alice-directory-password") == (200, "alice")
    assert directory.binds == ["alice", "alice"]


def test_only_the_accounts_own_live_app_passwords_sign_it_in(world):
    alice_passwords = mint(world, "alice", MANY)
    bob_password = mint(world, "bob")[0]

    for wrong in ("not-a-password", secrets.token_urlsafe(32), bob_password):
        assert kosync_sign_in(world, "alice", wrong) == 401
        assert opds_sign_in(world, "alice", wrong)[0] == 401
    assert kosync_sign_in(world, "bob", bob_password) == 200

    # Revoking from the account page stops that password at once, and only it.
    kept, revoked = alice_passwords[0], alice_passwords[1]
    assert kosync_sign_in(world, "alice", revoked) == 200
    row = (world.session.query(ub.UserAppPassword)
           .filter(ub.UserAppPassword.label == "Kindle 1",
                   ub.UserAppPassword.user_id == account(world, "alice").id).one())
    browser = world.browser("alice")
    assert browser.post("/api/v1/account/app-passwords/%d/delete" % row.id).status_code == 204
    assert kosync_sign_in(world, "alice", revoked) == 401
    assert opds_sign_in(world, "alice", revoked)[0] == 401
    assert kosync_sign_in(world, "alice", kept) == 200


def test_an_app_password_from_an_earlier_release_is_slow_once_then_fast(world, slow_hashes):
    mint(world, "alice", 3)
    _row, older = older_app_password(world, "alice")
    older_app_password(world, "alice", label="Lost phone")  # never used again

    assert kosync_sign_in(world, "alice", older) == 200
    slow_hashes.clear()
    assert kosync_sign_in(world, "alice", older) == 200
    assert opds_sign_in(world, "alice", older) == (200, "alice")
    assert slow_hashes == []

    # An older app password nobody uses any more costs the account password
    # nothing: it is checked only after the account password has failed.
    slow_hashes.clear()
    assert kosync_sign_in(world, "alice", "alice-account-password") == 200
    assert opds_sign_in(world, "alice", "alice-account-password") == (200, "alice")
    assert len(slow_hashes) == 2

    # Revoked older app passwords stay refused.
    revoked_row, revoked = older_app_password(world, "alice", label="Given away")
    revoked_row.revoked = True
    world.session.commit()
    assert kosync_sign_in(world, "alice", revoked) == 401
    assert opds_sign_in(world, "alice", revoked)[0] == 401


def test_the_digest_an_older_app_password_earns_is_saved_at_once(world):
    """The usual upgrade: a device was syncing moments ago under the old
    release, so its last use is fresh and no new stamp is due. Its digest must
    still be written then, not left pending for whatever commits next."""
    row, older = older_app_password(world, "alice")
    row.last_used_at = app_passwords.utcnow()
    world.session.commit()
    assert kosync_sign_in(world, "alice", older) == 200
    world.session.rollback()  # whatever was left pending is gone
    assert row.token_digest == app_passwords.token_digest(older)


def test_a_short_secret_is_never_given_a_fast_digest(world):
    """Every app password is 43 random characters; a short one can only be a
    secret somebody chose, and that must stay behind its slow hash."""
    row = ub.UserAppPassword(user_id=account(world, "alice").id, label="Typed",
                             password_hash=generate_password_hash("tulips2026"))
    world.session.add(row)
    world.session.commit()
    assert kosync_sign_in(world, "alice", "tulips2026") == 200
    world.session.refresh(row)
    assert row.token_digest is None


def test_last_use_is_kept_to_the_minute_without_a_write_per_request(world, monkeypatch):
    clock = [datetime(2026, 9, 23, 20, 0, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(app_passwords, "utcnow", lambda: clock[0], raising=False)
    passwords = mint(world, "alice", 2)
    writes = []

    def note_write(_conn, _cursor, statement, *_rest):
        if statement.lstrip().upper().startswith("UPDATE USER_APP_PASSWORD"):
            writes.append(statement)

    event.listen(world.engine, "before_cursor_execute", note_write)
    for _ in range(10):
        assert kosync_sign_in(world, "alice", passwords[0]) == 200
    assert len(writes) == 1

    clock[0] += timedelta(seconds=90)
    assert kosync_sign_in(world, "alice", passwords[0]) == 200
    assert len(writes) == 2

    listed = world.browser("alice").get("/api/v1/account").get_json()["app_passwords"]
    last_used = {entry["label"]: entry["last_used_at"] for entry in listed}
    assert last_used["Kindle 0"].startswith("2026-09-23T20:01:30")
    assert last_used["Kindle 1"] is None


OLD_TABLE = """
CREATE TABLE user_app_password (
    id INTEGER NOT NULL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES user (id) ON DELETE CASCADE,
    label VARCHAR NOT NULL,
    password_hash VARCHAR NOT NULL,
    created_at DATETIME NOT NULL,
    last_used_at DATETIME,
    revoked BOOLEAN NOT NULL
)
"""


def test_an_existing_database_keeps_its_app_passwords_across_the_upgrade(tmp_path, monkeypatch):
    """Boot the real migrator, twice, on an app.db whose app password table
    predates the digest, with an app password already in it."""
    from cps import config_sql, constants

    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path), raising=False)
    engine = create_engine("sqlite:///%s" % (tmp_path / "app.db"), future=True)
    ub.Base.metadata.create_all(engine)
    config_sql._Settings.__table__.create(engine, checkfirst=True)
    cleartext = secrets.token_urlsafe(32)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE user_app_password"))
        connection.execute(text(OLD_TABLE))
        connection.execute(text(
            "CREATE INDEX ix_user_app_password_user_id ON user_app_password (user_id)"))
        connection.execute(text(
            "INSERT INTO user_app_password (user_id, label, password_hash, created_at, revoked) "
            "VALUES (7, 'Kobo', :hash, '2026-01-02 03:04:05', 0)"),
            {"hash": generate_password_hash(cleartext)})

    session = sessionmaker(bind=engine, future=True)()
    try:
        ub.migrate_Database(session)
        ub.migrate_Database(session)
        indexes = {row[0] for row in session.execute(text(
            "SELECT name FROM sqlite_master WHERE type = 'index' "
            "AND tbl_name = 'user_app_password'"))}
        assert "ix_user_app_password_token_digest" in indexes

        owner = type("Owner", (), {"id": 7})()
        monkeypatch.setattr(ub, "session", session)
        assert usermanagement._verify_app_password(owner, cleartext) is True
        assert usermanagement._verify_app_password(owner, "something else") is False
        stored = session.execute(text(
            "SELECT token_digest, last_used_at FROM user_app_password")).one()
        assert stored[0] is not None and cleartext not in stored[0]
        assert stored[1] is not None
    finally:
        session.close()
        engine.dispose()
