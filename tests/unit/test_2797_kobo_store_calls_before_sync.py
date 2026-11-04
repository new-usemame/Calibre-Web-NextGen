# SPDX-License-Identifier: GPL-3.0-or-later
"""With store proxying on, a store refusal must not stop a Kobo before it syncs (#2797, #2807).

A Kobo makes a few store calls between ``/v1/initialization`` and
``/v1/library/sync``. Answered empty (proxy off) the sync goes on. Redirected to
the store (proxy on) the reporters' devices never ask for ``/v1/library/sync``,
and the redirect left the store's answer out of the server's sight.

These drive the real Kobo blueprint and stand in only for the store's HTTP reply.
"""

from types import SimpleNamespace

import pytest
import requests

from .kobo_pairing_world import HEADERS, kobo_app  # noqa: F401

STORE_CALLS = [
    "/v1/user/profile",
    "/v1/products/books/subscriptions",
    "/v1/deals",
    "/v1/products/some-product-id/nextread",
]
AUTH = {**HEADERS, "Authorization": "Bearer device-session"}


def store_reply(status, body=b"{}", headers=None):
    reply = requests.Response()
    reply.status_code = status
    reply._content = body
    reply.headers.update({"Content-Type": "application/json", **(headers or {})})
    return reply


@pytest.fixture
def store(kobo_app, monkeypatch):
    monkeypatch.setattr(kobo_app.kobo.config, "config_kobo_proxy", True, raising=False)
    world = SimpleNamespace(reply=store_reply(200), raises=None, calls=[], logged=[])

    def request(method, url, headers=None, **_kw):
        world.calls.append(SimpleNamespace(method=method, url=url, headers=headers))
        if world.raises:
            raise world.raises
        return world.reply

    monkeypatch.setattr(kobo_app.kobo.requests, "request", request)

    def record(*args, **_kw):
        world.logged.append(args[0] % args[1:] if len(args) > 1 else str(args[0]))

    for level in ("debug", "info", "warning"):
        monkeypatch.setattr(kobo_app.kobo.log, level, record)
    return world


def ask(kobo_app, path):
    return kobo_app.app.test_client().get("/kobo/reader-token" + path, headers=AUTH)


@pytest.mark.parametrize("path", STORE_CALLS)
@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_a_store_refusal_gets_the_same_empty_answer_as_proxy_off(kobo_app, store, path, status):
    store.reply = store_reply(
        status,
        b'{"ResponseStatus":{"ErrorCode":"UnauthorizedAccessException","Message":"Unauthorized Request."}}',
    )

    response = ask(kobo_app, path)

    assert response.status_code == 200, response.data[:200]
    assert response.get_json() == {}


def test_a_refusal_is_logged_with_the_store_status_and_no_sync_token(kobo_app, store):
    store.reply = store_reply(
        401, b'{"ResponseStatus":{"ErrorCode":"UnauthorizedAccessException"}}')

    ask(kobo_app, "/v1/deals")

    logged = "\n".join(store.logged)
    assert "401" in logged and "UnauthorizedAccessException" in logged and "/v1/deals" in logged
    assert "reader-token" not in logged


@pytest.mark.parametrize(
    "failure", [requests.ConnectTimeout("slow"), requests.ConnectionError("down")])
def test_an_unreachable_store_does_not_stop_the_sync(kobo_app, store, failure):
    store.raises = failure

    response = ask(kobo_app, "/v1/user/profile")

    assert response.status_code == 200
    assert response.get_json() == {}


def test_a_store_answer_reaches_the_kobo_with_the_device_session(kobo_app, store):
    store.reply = store_reply(200, b'{"from": "store"}', {"X-Store": "yes"})

    response = ask(kobo_app, "/v1/deals")

    assert response.status_code == 200
    assert response.get_json() == {"from": "store"}
    assert response.headers["X-Store"] == "yes"
    sent = store.calls[0]
    assert sent.url == "https://storeapi.kobo.com/v1/deals"
    assert sent.headers.get("Authorization") == "Bearer device-session"


def test_the_account_profile_the_store_returns_is_not_written_to_the_log(kobo_app, store):
    store.reply = store_reply(200, b'{"Email": "reader@private.invalid"}')

    response = ask(kobo_app, "/v1/user/profile")

    assert response.get_json() == {"Email": "reader@private.invalid"}
    assert "reader@private.invalid" not in "\n".join(store.logged)


def test_an_expired_session_still_reaches_the_kobo_so_it_signs_in_again(kobo_app, store):
    body = b'{"ResponseStatus":{"ErrorCode":"ExpiredToken","Message":"expired"}}'
    store.reply = store_reply(401, body)

    response = ask(kobo_app, "/v1/user/profile")

    assert response.status_code == 401
    assert response.data == body


def test_a_store_answer_that_is_not_a_refusal_is_left_alone(kobo_app, store):
    store.reply = store_reply(404, b'{"gone": true}')

    response = ask(kobo_app, "/v1/products/some-product-id/nextread")

    assert response.status_code == 404


def test_with_the_proxy_off_the_store_is_never_contacted(kobo_app, store, monkeypatch):
    monkeypatch.setattr(kobo_app.kobo.config, "config_kobo_proxy", False, raising=False)

    response = ask(kobo_app, "/v1/deals")

    assert response.status_code == 200
    assert response.get_json() == {}
    assert store.calls == []


def test_other_store_calls_still_redirect_to_the_store(kobo_app, store):
    response = ask(kobo_app, "/v1/products/dailydeal")

    assert response.status_code == 307
    assert response.headers["Location"] == "https://storeapi.kobo.com/v1/products/dailydeal"
    assert store.calls == []
