"""Explicitly invoked in isolation by tests/integration/test_real_app_kobo.py.

Sign-in with the production limiter while its store is dead. An admin can put
the limits in Redis or Memcached; when that store stops answering, a Kobo with
a valid token keeps syncing, a right password signs in, and wrong credentials
are still paced. The store is killed whole -- every read and write raises --
because a real outage does not fail one method and answer the others.

Runs in its own interpreter: the limiter's switch to its fallback is process
state, and the healthy control must run before it.
"""
import secrets

import pytest

import kobo_fixture as fixture

pytestmark = pytest.mark.integration

PER_MINUTE = 3


def _kobo_token(user_id):
    from cps import ub

    token = ub.RemoteAuthToken()
    token.user_id = user_id
    token.auth_token = secrets.token_hex(16)
    token.token_type = 1
    ub.session.add(token)
    ub.session.commit()
    return token.auth_token


def _initialization(client, token, times):
    return [client.get("/kobo/%s/v1/initialization" % token,
                       headers=fixture.DEVICE_HEADERS).status_code
            for _ in range(times)]


def _app_sign_in(client, address):
    """Sign in through the app's own API, as the single-page app does."""
    environ = {"REMOTE_ADDR": address}
    token = client.get("/api/v1/auth/csrf", environ_overrides=environ) \
        .get_json()["csrf_token"]
    return client.post(
        "/api/v1/auth/login",
        json={"username": fixture.READER_NAME, "password": fixture.READER_PASSWORD},
        headers={"X-CSRFToken": token}, environ_overrides=environ,
    ).status_code


def test_sign_in_survives_a_dead_limiter_store(kobo_real_app, monkeypatch):
    """Intent: a limiter-store outage costs neither a 500 nor a lockout.

    Breaks if: the clean-up after a successful sign-in, or the rate-limit
    headers, reach the dead store unguarded (500s); a Kobo's successful
    sign-in stops clearing its count (a healthy Kobo is refused from its
    fourth request a minute); or the outage turns pacing off altogether
    (wrong tokens never 429).
    """
    from cps import config

    assert config.config_ratelimiter
    user_id = fixture.create_reader()
    fixture.register_kobo_device(kobo_real_app, user_id)
    token = _kobo_token(user_id)
    client = kobo_real_app.test_client()

    # Healthy control: a valid token is never slowed; an unknown one is.
    assert _initialization(client, token, PER_MINUTE + 3) == [200] * (PER_MINUTE + 3)
    assert _initialization(client, "0" * 32, PER_MINUTE + 1) == \
        [401] * PER_MINUTE + [429]

    # The store dies as a right web password is cleared: still signed in.
    fixture.store_dies(monkeypatch, at_first_clear=True)
    browser = kobo_real_app.test_client()
    browser.environ_base["REMOTE_ADDR"] = "192.0.2.45"
    fixture.login(browser)  # asserts the right password is answered 302
    fixture.store_returns(monkeypatch)

    # The same through the app's own sign-in API.
    fixture.store_dies(monkeypatch, at_first_clear=True)
    assert _app_sign_in(kobo_real_app.test_client(), "192.0.2.47") == 200
    fixture.store_returns(monkeypatch)

    # The store dies as a Kobo's valid token is cleared: it keeps syncing.
    fixture.store_dies(monkeypatch, at_first_clear=True)
    kobo = kobo_real_app.test_client()
    kobo.environ_base["REMOTE_ADDR"] = "192.0.2.46"
    assert _initialization(kobo, token, PER_MINUTE + 3) == [200] * (PER_MINUTE + 3)
    fixture.store_returns(monkeypatch)

    # Then it is gone altogether: devices sync, strangers are still paced.
    fixture.store_dies(monkeypatch, at_first_clear=False)
    other = kobo_real_app.test_client()
    other.environ_base["REMOTE_ADDR"] = "192.0.2.44"
    assert _initialization(other, token, PER_MINUTE + 3) == [200] * (PER_MINUTE + 3)
    assert _initialization(other, "1" * 32, PER_MINUTE + 1) == \
        [401] * PER_MINUTE + [429]
    fixture.login(kobo_real_app.test_client())
