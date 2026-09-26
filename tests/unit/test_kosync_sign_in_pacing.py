# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""KOReader sync paces wrong passwords per client, and never slows the owner.

KOReader sends its Basic credentials with every request. Wrong ones used to be
checked without limit (and, under LDAP, each cost a directory bind). They are
now counted per client address and account: a client that keeps sending a
wrong password is answered 429, while the account's owner, signing in with the
right password from another client, is never held up by it. A right password
clears its own client's count, so a device syncing a library in a burst of
signed-in requests is never slowed.

Requests go through the real KOReader sync blueprint and a real limiter.
"""

import flask
import pytest
from flask_limiter import Limiter

from cps import rate_limits, ub
from cps.services import app_passwords
from tests.unit.koreader_library_world import LibraryWorld

pytestmark = pytest.mark.unit

PER_MINUTE = 3
STALE_KINDLE = "192.0.2.10"
OWNERS_PHONE = "192.0.2.20"


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = LibraryWorld(monkeypatch, tmp_path)
    limiter = Limiter(key_func=lambda: "unused", auto_check=False, storage_uri="memory://")
    limiter.limit(f"{PER_MINUTE}/minute", key_func=rate_limits.basic_auth_client_key)(
        w.kosync.kosync)
    limiter.init_app(w.app)
    monkeypatch.setattr(w.kosync, "limiter", limiter, raising=False)
    w.add_user("alice", password="alice-password")
    yield w
    w.close()


def _client(world, address):
    client = world.app.test_client()
    client.environ_base["REMOTE_ADDR"] = address
    return client


def _sign_ins(world, address, passwords, *, path="/kosync/users/auth", method="get"):
    client = _client(world, address)
    body = {"json": {"document": "d" * 32, "progress": "1", "percentage": 0.5,
                     "device": "Kindle", "device_id": "kindle-1"}} if method == "put" else {}
    return [getattr(client, method)(path, headers=world.basic("alice", pw), **body).status_code
            for pw in passwords]


def test_wrong_passwords_from_one_client_are_paced(world):
    assert _sign_ins(world, STALE_KINDLE, ["wrong"] * (PER_MINUTE + 2)) == \
        [401] * PER_MINUTE + [429, 429]


def test_a_paced_progress_upload_is_answered_429_not_500(world):
    statuses = _sign_ins(world, STALE_KINDLE, ["wrong"] * (PER_MINUTE + 1),
                         path="/kosync/syncs/progress", method="put")
    assert statuses == [401] * PER_MINUTE + [429]


def test_a_device_with_a_revoked_app_password_does_not_lock_its_owner_out(world):
    user = world.session.query(ub.User).filter(ub.User.name == "alice").one()
    row, stale_password = app_passwords.mint(user.id, "Kindle", session=world.session)
    world.session.commit()
    assert _sign_ins(world, STALE_KINDLE, [stale_password]) == [200]
    row.revoked = True
    world.session.commit()

    # The Kindle keeps retrying on every auto-sync and is paced...
    assert _sign_ins(world, STALE_KINDLE, [stale_password] * (PER_MINUTE + 2))[-1] == 429
    # ...while the owner's phone signs in at once, every time.
    assert _sign_ins(world, OWNERS_PHONE, ["alice-password"] * 5) == [200] * 5


def test_a_right_password_clears_its_clients_count(world):
    typo_then_right = ["wrong"] * (PER_MINUTE - 1) + ["alice-password"]
    statuses = _sign_ins(world, OWNERS_PHONE,
                         typo_then_right + ["wrong"] * (PER_MINUTE - 1) + ["alice-password"] * 10)
    assert 429 not in statuses
    assert statuses[-10:] == [200] * 10


def test_one_client_is_paced_per_account():
    app = flask.Flask(__name__)
    keys = []
    for address, account in [(STALE_KINDLE, "alice"), (OWNERS_PHONE, "alice"),
                             (STALE_KINDLE, "Alice "), (STALE_KINDLE, "bob")]:
        with app.test_request_context(
                "/", environ_base={"REMOTE_ADDR": address},
                headers=LibraryWorld.basic(account, "x")):
            keys.append(rate_limits.basic_auth_client_key())
    stale_alice, phone_alice, stale_alice_again, stale_bob = keys
    assert stale_alice == stale_alice_again
    assert len({stale_alice, phone_alice, stale_bob}) == 3
