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
    # A same-host proxy naming its client another way is relaying it too.
    assert _get(real_app, "127.0.0.1", status, {"X-Real-IP": "203.0.113.9"}).status_code == 403
    assert _get(real_app, "127.0.0.1", status,
                {"Forwarded": 'for="203.0.113.9:4711";proto=https'}).status_code == 403


RELAYED = [
    ("127.0.0.1", {"X-Forwarded-For": "203.0.113.9"}),
    ("::1", {"X-Forwarded-For": "127.0.0.1, 198.51.100.1"}),
    ("::ffff:127.0.0.1", {"Forwarded": "for=198.51.100.1"}),
    ("127.0.0.1", {"X-Real-IP": "198.51.100.1"}),
    # Two proxies on this host: the second appends the first's address.
    ("127.0.0.1", {"X-Forwarded-For": "198.51.100.1, 127.0.0.1"}),
    # The client's own Forwarded element, then the proxy's.
    ("127.0.0.1", {"Forwarded": "for=127.0.0.1, for=198.51.100.1"}),
    # RFC 7239 parameter names are case-insensitive.
    ("127.0.0.1", {"Forwarded": "For=198.51.100.1"}),
    # A proxy that hides who its client is has still relayed someone.
    ("127.0.0.1", {"Forwarded": "for=_hidden"}),
    ("127.0.0.1", {"X-Forwarded-For": "unknown"}),
]


def test_a_trust_list_without_this_host_still_refuses_relayed_internal_calls(real_app):
    """Intent: the internal routes' local check does not depend on the trust list.

    A list that leaves out loopback (a proxy elsewhere, say) means a proxy on
    this host is not believed and its headers are removed before the app
    sees them. The check must still see that it relayed someone.

    Breaks if: the check reads the headers after removal (a relayed call
    then looks like this host's own and every internal route answers it).
    """
    from cps.reverseproxy import parse_trusted_networks

    gate = real_app.wsgi_app
    default = gate.networks
    gate.networks = parse_trusted_networks("10.0.0.0/8, 203.0.113.7")
    try:
        status = "/cwa-internal/duplicate-scan-status"
        for peer, headers in RELAYED:
            assert _get(real_app, peer, status, headers).status_code == 403, (peer, headers)
        client = real_app.test_client()
        relayed = client.post("/cwa-internal/reconnect-db", json={},
                              headers={"X-Forwarded-For": "203.0.113.9"},
                              environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert relayed.status_code == 403
        assert _get(real_app, "127.0.0.1", status, {"X-Forwarded-For": "127.0.0.1"}).status_code == 200
        assert _get(real_app, "::1", status).status_code == 200
    finally:
        gate.networks = default


# What a client that reaches the listener directly might send to pass as
# this host. scripts/ingest_processor.py sends the first one on every call.
FORGED_LOCAL = [
    {},
    {"X-Forwarded-For": "127.0.0.1"},
    {"X-Forwarded-For": "::1"},
    {"X-Real-IP": "127.0.0.1"},
    {"Forwarded": "for=127.0.0.1"},
    {"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1",
     "Forwarded": "for=127.0.0.1", "X-Forwarded-Host": "localhost"},
]
# Not this host: a public client, a LAN client, a proxy on the docker network.
NOT_THIS_HOST = ["198.51.100.50", "192.168.1.20", DOCKER_PROXY]


def _internal_calls(app):
    """Every (method, path) the app serves under /cwa-internal/."""
    calls = []
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith("/cwa-internal/"):
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                calls.append((method, rule.rule))
    return sorted(calls)


def test_every_internal_route_refuses_a_caller_that_is_not_this_host(real_app):
    """Intent: no internal route answers anyone but this host's own processes.

    Characterizes #2552: since #2324 a direct client's proxy headers are
    removed, and the internal routes judge the connection itself. The routes
    are read from the app's own URL map, so one added later is covered too.

    Breaks if: any internal route is registered without the local-call check;
    or the check believes a header naming loopback from a peer that is not
    loopback (the ingest processor's own header, forged); or the refusal is
    caught inside the view and turned into a 500 or a 200.
    """
    calls = _internal_calls(real_app)
    assert {path for _, path in calls} >= {
        "/cwa-internal/schedule-auto-send",
        "/cwa-internal/queue-duplicate-scan",
        "/cwa-internal/run-duplicate-scan",
        "/cwa-internal/duplicate-scan-status",
        "/cwa-internal/schedule-convert-library",
        "/cwa-internal/schedule-epub-fixer",
        "/cwa-internal/reconnect-db",
    }, calls

    client = real_app.test_client()
    answered = []
    for method, path in calls:
        for peer in NOT_THIS_HOST:
            for headers in FORGED_LOCAL:
                response = client.open(path, method=method, json={}, headers=headers,
                                       environ_base={"REMOTE_ADDR": peer})
                if response.status_code != 403:
                    answered.append((method, path, peer, headers, response.status_code))
    assert answered == []

    # The ingest processor's own call, from this host, still reaches the view.
    local = {"X-Forwarded-For": "127.0.0.1"}
    for path, body, expected in [
        ("/cwa-internal/reconnect-db", {}, {"status": "refreshed"}),
        ("/cwa-internal/run-duplicate-scan", {},
         {"success": True, "skipped": True, "reason": "no_book_ids"}),
    ]:
        response = client.post(path, json=body, headers=local,
                               environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert (response.status_code, response.get_json()) == (200, expected), path
