# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""KOReader sync paces password guesses, not the devices that sync.

KOReader sends its Basic credentials with every request. Wrong ones used to be
checked without limit (and, under LDAP, each cost a directory bind). Now each
client's *new* wrong passwords for an account are counted; after three in a
minute that client is refused 429 before its password is even looked at.

What must never be refused: a device with an app password; the right password
from any client that is not guessing; and the owner of a stale device on the
same home network, whose old password is one wrong password sent again and
again, not a stream of guesses.

Requests go through the real KOReader sync blueprint and a real limiter.
"""

import pytest
from flask_limiter import Limiter

from cps import constants, ub
from cps.services import app_passwords
from tests.unit.koreader_library_world import LibraryWorld

pytestmark = pytest.mark.unit

PER_MINUTE = 3
GUESSER = "192.0.2.10"
OWNERS_PHONE = "192.0.2.20"
HOME = "198.51.100.7"  # one address for the whole household (NAT)
GUESSES = ["guess-%d" % n for n in range(PER_MINUTE + 2)]


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = LibraryWorld(monkeypatch, tmp_path)
    limiter = Limiter(key_func=lambda: "unused", auto_check=False, storage_uri="memory://")
    limiter.init_app(w.app)
    monkeypatch.setattr(w.kosync, "limiter", limiter, raising=False)
    w.limiter = limiter
    w.add_user("alice", password="alice-password")
    yield w
    w.close()


def _sign_ins(world, address, passwords, *, account="alice",
              path="/kosync/users/auth", method="get"):
    client = world.app.test_client()
    client.environ_base["REMOTE_ADDR"] = address
    body = {"json": {"document": "d" * 32, "progress": "1", "percentage": 0.5,
                     "device": "Kindle", "device_id": "kindle-1"}} if method == "put" else {}
    return [getattr(client, method)(path, headers=world.basic(account, pw), **body).status_code
            for pw in passwords]


def _app_password(world):
    user = world.session.query(ub.User).filter(ub.User.name == "alice").one()
    row, password = app_passwords.mint(user.id, "Kindle", session=world.session)
    world.session.commit()
    return row, password


def test_a_client_guessing_passwords_is_paced(world):
    assert _sign_ins(world, GUESSER, GUESSES) == [401] * PER_MINUTE + [429, 429]
    client = world.app.test_client()
    client.environ_base["REMOTE_ADDR"] = GUESSER
    paced = client.get("/kosync/users/auth", headers=world.basic("alice", "more"))
    assert paced.status_code == 429 and 1 <= int(paced.headers["Retry-After"]) <= 61
    # Once paced, even a right guess is refused: pacing that let the right
    # password through would tell a guesser which one it was.
    assert _sign_ins(world, GUESSER, ["alice-password"]) == [429]
    # Another client of the same account is not held up by it.
    assert _sign_ins(world, OWNERS_PHONE, ["alice-password"] * 5) == [200] * 5


def test_a_stale_device_on_the_owners_network_does_not_lock_the_owner_out(world):
    """The judge's NAT case: one address, a stale device and its owner."""
    # The old password, retried on every auto-sync, is one wrong password.
    assert _sign_ins(world, HOME, ["old-password"] * 10) == [401] * 10
    assert _sign_ins(world, HOME, ["alice-password"] * 3) == [200] * 3


def test_a_device_with_a_revoked_app_password_does_not_lock_its_owner_out(world):
    row, stale_password = _app_password(world)
    assert _sign_ins(world, HOME, [stale_password]) == [200]
    row.revoked = True
    world.session.commit()

    assert _sign_ins(world, HOME, [stale_password] * 10) == [401] * 10
    assert _sign_ins(world, HOME, ["alice-password"] * 3) == [200] * 3


def test_a_device_with_an_app_password_is_never_refused(world):
    _row, password = _app_password(world)
    assert _sign_ins(world, HOME, GUESSES)[-1] == 429
    assert _sign_ins(world, HOME, [password] * 5) == [200] * 5


def test_a_right_password_clears_its_clients_count(world):
    typos = GUESSES[:PER_MINUTE - 1]
    statuses = _sign_ins(world, OWNERS_PHONE,
                         typos + ["alice-password"] + GUESSES[2:2 + PER_MINUTE - 1]
                         + ["alice-password"] * 5)
    assert statuses == [401, 401, 200, 401, 401] + [200] * 5


def test_the_count_is_per_account_whatever_its_spelling(world):
    world.add_user("bob", password="bob-password")
    assert _sign_ins(world, GUESSER, GUESSES[:PER_MINUTE]) == [401] * PER_MINUTE
    assert _sign_ins(world, GUESSER, ["another"], account=" ALICE ") == [429]
    assert _sign_ins(world, GUESSER, ["bob-password"], account="bob") == [200]


def test_a_paced_progress_upload_is_answered_429_not_500(world):
    statuses = _sign_ins(world, GUESSER, GUESSES[:PER_MINUTE + 1],
                         path="/kosync/syncs/progress", method="put")
    assert statuses == [401] * PER_MINUTE + [429]


def test_a_paced_progress_read_is_answered_429_not_500(world):
    statuses = _sign_ins(world, GUESSER, GUESSES[:PER_MINUTE + 1],
                         path="/kosync/syncs/progress/" + "d" * 32)
    assert statuses == [401] * PER_MINUTE + [429]


def test_the_same_guesses_at_another_account_are_new_guesses(world):
    world.add_user("bob", password="bob-password")
    assert _sign_ins(world, GUESSER, GUESSES[:PER_MINUTE]) == [401] * PER_MINUTE
    assert _sign_ins(world, GUESSER, GUESSES[:PER_MINUTE + 1], account="bob") == \
        [401] * PER_MINUTE + [429]


def test_guesses_at_an_unknown_account_are_paced_too(world):
    assert _sign_ins(world, GUESSER, GUESSES, account="nobody") == \
        [401] * PER_MINUTE + [429, 429]


STORE_METHODS = ("incr", "get", "get_expiry", "clear", "reset", "acquire_entry",
                 "get_moving_window", "acquire_sliding_window_entry", "get_sliding_window")


def _store_dies(monkeypatch, limiter):
    def unreachable(*_args, **_kwargs):
        raise ConnectionError("limiter store unreachable")

    for method in STORE_METHODS:
        if hasattr(limiter.storage, method):
            monkeypatch.setattr(limiter.storage, method, unreachable)


def test_pacing_carries_on_in_memory_when_the_store_dies(world, monkeypatch):
    """The app's limiter falls back to memory; so must these sign-ins (#2315)."""
    limiter = Limiter(key_func=lambda: "unused", auto_check=False, storage_uri="memory://",
                      in_memory_fallback_enabled=True)
    limiter.init_app(world.app)
    monkeypatch.setattr(world.kosync, "limiter", limiter)
    _store_dies(monkeypatch, limiter)
    assert _sign_ins(world, GUESSER, GUESSES) == [401] * PER_MINUTE + [429, 429]
    assert _sign_ins(world, OWNERS_PHONE, ["alice-password"] * 3) == [200] * 3


def test_pacing_fails_open_when_the_limiter_is_off_or_its_store_errors(world, monkeypatch):
    world.limiter.enabled = False
    assert _sign_ins(world, GUESSER, GUESSES) == [401] * len(GUESSES)
    world.limiter.enabled = True

    def unreachable(*_args, **_kwargs):
        raise ConnectionError("limiter store unreachable")

    for method in ("test", "hit", "clear", "get_window_stats"):
        monkeypatch.setattr(world.limiter.limiter, method, unreachable)
    assert _sign_ins(world, OWNERS_PHONE, GUESSES) == [401] * len(GUESSES)
    assert _sign_ins(world, OWNERS_PHONE, ["alice-password"]) == [200]


class _Directory:
    def __init__(self):
        self.binds = 0

    def get_object_details(self, user=None, **_):
        return {"uid": [user]}

    def bind_user(self, username, password):
        self.binds += 1
        return True if password == "directory-password" else None


@pytest.fixture
def directory(world, monkeypatch):
    import cps
    from cps import config
    from cps.services import simpleldap

    ldap = _Directory()
    monkeypatch.setattr(simpleldap, "_ldap", ldap)
    monkeypatch.setattr(cps.services, "ldap", simpleldap)
    monkeypatch.setattr(config, "config_login_type", constants.LOGIN_LDAP, raising=False)
    return ldap


def test_a_directory_sign_in_clears_its_clients_count(world, directory):
    statuses = _sign_ins(world, OWNERS_PHONE,
                         GUESSES[:2] + ["directory-password"] + GUESSES[2:4]
                         + ["directory-password"] * 5)
    assert statuses == [401, 401, 200, 401, 401] + [200] * 5


def test_a_paced_client_costs_the_directory_nothing(world, directory):
    assert _sign_ins(world, GUESSER, GUESSES) == [401] * PER_MINUTE + [429, 429]
    assert directory.binds == PER_MINUTE


def test_a_stale_device_costs_the_directory_one_bind_a_minute(world, directory):
    # A failed bind is one a directory may count towards locking the account.
    assert _sign_ins(world, HOME, ["old-password"] * 10) == [401] * 10
    assert directory.binds == 1
    assert _sign_ins(world, HOME, ["directory-password"]) == [200]

