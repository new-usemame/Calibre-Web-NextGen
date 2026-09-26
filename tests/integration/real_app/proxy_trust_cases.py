"""Explicitly invoked in isolation by tests/integration/test_real_app_kobo.py.

Reverse-proxy headers through the production application: create_app,
register_blueprints, the real limiter and every middleware in its order. A
client that reaches the listener directly is taken at its own address,
whatever X-Forwarded-For it sends; a proxy where proxies sit is believed.
"""
import pytest

import kobo_fixture as fixture

pytestmark = pytest.mark.integration

PER_MINUTE = 3
GUESSES = ["guess-%d" % n for n in range(PER_MINUTE + 2)]
DOCKER_PROXY = "172.18.0.2"


def _sign_ins(app, peer, passwords, forwarded_for=None):
    """Sign in through the app's own API, as the single-page app does."""
    client = app.test_client()
    environ = {"REMOTE_ADDR": peer}
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
    token = client.get("/api/v1/auth/csrf", headers=headers, environ_overrides=environ) \
        .get_json()["csrf_token"]
    statuses = []
    for n, password in enumerate(passwords):
        if forwarded_for is None:
            # A direct client naming a new address on every attempt.
            headers = {"X-Forwarded-For": "203.0.113.%d" % n}
        statuses.append(client.post(
            "/api/v1/auth/login",
            json={"username": fixture.READER_NAME, "password": password},
            headers=dict(headers, **{"X-CSRFToken": token}), environ_overrides=environ,
        ).status_code)
    return statuses


def _get(app, peer, path, headers=None):
    client = app.test_client()
    return client.get(path, headers=headers or {}, environ_base={"REMOTE_ADDR": peer})


def test_forwarded_headers_count_only_from_a_proxy(real_app):
    """Intent: a direct client cannot choose the address the app sees.

    Breaks if: forwarded headers are believed from any peer (a direct client's
    pacing and the internal routes' local check follow its headers rather than
    its connection); or they stop being believed
    from a proxy on the docker network (its clients share one address, and one
    guesser gets the owner refused); or a proxy on this host relaying someone
    else's request passes as a local call.
    """
    from cps import config

    assert config.config_ratelimiter
    fixture.create_reader()

    # A direct client naming a different address on every guess is paced.
    assert _sign_ins(real_app, "198.51.100.50", GUESSES) == [401] * PER_MINUTE + [429, 429]

    # Behind a proxy on the docker network, each client is paced on its own.
    assert _sign_ins(real_app, DOCKER_PROXY, GUESSES, forwarded_for="203.0.113.200") == \
        [401] * PER_MINUTE + [429, 429]
    assert _sign_ins(real_app, DOCKER_PROXY, [fixture.READER_PASSWORD],
                     forwarded_for="203.0.113.201") == [200]

    # The internal routes answer this host's own processes only.
    status = "/cwa-internal/duplicate-scan-status"
    assert _get(real_app, "198.51.100.50", status, {"X-Forwarded-For": "127.0.0.1"}).status_code == 403
    assert _get(real_app, DOCKER_PROXY, status, {"X-Forwarded-For": "127.0.0.1"}).status_code == 403
    assert _get(real_app, "127.0.0.1", status, {"X-Forwarded-For": "203.0.113.9"}).status_code == 403
    assert _get(real_app, "127.0.0.1", status, {"X-Forwarded-For": "127.0.0.1"}).status_code == 200
    assert _get(real_app, "127.0.0.1", status).status_code == 200
