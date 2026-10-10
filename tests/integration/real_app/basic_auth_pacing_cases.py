"""Explicitly invoked in isolation by tests/integration/test_real_app_kobo.py.

KOReader sync and OPDS sign-ins, paced by the production application: the real
create_app and register_blueprints, the real limiter, ProxyFix and error
handlers. Both clients send HTTP Basic credentials with every request, so what
is paced is a client's new wrong passwords for one account. A right password
clears that count; an app password, or the same stale password sent again,
is never refused.
"""
import base64
import sys

import pytest

import kobo_fixture as fixture

pytestmark = pytest.mark.integration

PER_MINUTE = 3
GUESSES = ["guess-%d" % n for n in range(PER_MINUTE + 2)]


def _basic(password):
    token = base64.b64encode(("%s:%s" % (fixture.READER_NAME, password)).encode()).decode()
    return {"Authorization": "Basic " + token}


def _statuses(app, address, path, passwords):
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = address
    return [client.get(path, headers=_basic(p)).status_code for p in passwords]


@pytest.fixture(scope="module")
def app_password(real_app):
    """The reader's account, and an app password for one of their devices."""
    from cps import ub
    from cps.services import app_passwords

    user_id = fixture.create_reader()
    _row, password = app_passwords.mint(user_id, "Kindle", session=ub.session)
    ub.session.commit()
    return password


@pytest.fixture
def sync_on(monkeypatch):
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    monkeypatch.setattr(sys.modules["cps.progress_syncing.protocols.kosync"],
                        "is_koreader_sync_enabled", lambda: True)


def test_sync_and_catalogue_sign_ins_are_paced_per_client(real_app, app_password, sync_on):
    """Intent: a client guessing passwords is refused; nobody else is.

    Breaks if: either blueprint stops pacing (no 429) or stops clearing the
    count on a right password (typos add up to a 429), pacing goes back to one
    count per account (the owner's other client is refused), a repeated stale
    password counts as new guesses (the owner behind the same address is
    locked out), or an app password waits on the pacing (a paced address
    refuses a syncing device).
    """
    from cps import config

    assert config.config_ratelimiter

    for path, first in (("/kosync/users/auth", 10), ("/opds", 20)):
        guesser, phone, home, typist = ("192.0.2.%d" % (first + n) for n in range(4))
        assert _statuses(real_app, guesser, path, GUESSES[:PER_MINUTE + 1]) == \
            [401] * PER_MINUTE + [429], path
        assert _statuses(real_app, guesser, path, [fixture.READER_PASSWORD]) == [429], path
        assert _statuses(real_app, guesser, path, [app_password] * 2) == [200] * 2, path
        assert _statuses(real_app, phone, path, [fixture.READER_PASSWORD] * 4) == [200] * 4, path
        assert _statuses(real_app, home, path, ["old-password"] * 6) == [401] * 6, path
        assert _statuses(real_app, home, path, [fixture.READER_PASSWORD]) == [200], path
        # A right password clears its client's count: typos never add up.
        typos = GUESSES[:2] + [fixture.READER_PASSWORD] + GUESSES[2:4] + [fixture.READER_PASSWORD]
        assert _statuses(real_app, typist, path, typos) == [401, 401, 200] * 2, path

    # A paced sync client is answered in the protocol's own shape.
    client = real_app.test_client()
    client.environ_base["REMOTE_ADDR"] = "192.0.2.10"
    response = client.get("/kosync/users/auth", headers=_basic("another-guess"))
    assert response.status_code == 429
    assert response.get_json()["error"] == 2001
    assert 1 <= int(response.headers["Retry-After"]) <= 61
    client.environ_base["REMOTE_ADDR"] = "192.0.2.20"  # the catalogue's guesser
    response = client.get("/opds", headers=_basic("another-guess"))
    assert response.status_code == 429
    assert 1 <= int(response.headers["Retry-After"]) <= 61


def test_sign_ins_are_still_paced_while_the_limiter_store_is_down(
        real_app, app_password, sync_on, monkeypatch):
    """Intent: an outage of an external limiter store does not unpace guessing.

    The app's limiter carries on in memory when its store dies (#2315), but
    only inside its own checks. These sign-ins do not go through those, so
    the first request after the outage may well be one of them.

    Breaks if: a store error lets every guess through (no 429), or it
    refuses the right password or a device's app password.
    """
    from cps import limiter

    limiter.reset()
    fixture.store_dies(monkeypatch, at_first_clear=False)
    try:
        for path, first in (("/opds", 50), ("/kosync/users/auth", 60)):
            guesser, phone = ("192.0.2.%d" % (first + n) for n in range(2))
            assert _statuses(real_app, guesser, path, GUESSES[:PER_MINUTE + 1]) == \
                [401] * PER_MINUTE + [429], path
            assert _statuses(real_app, guesser, path, [app_password]) == [200], path
            assert _statuses(real_app, phone, path, [fixture.READER_PASSWORD] * 4) == [200] * 4, path
    finally:
        fixture.store_returns(monkeypatch)
