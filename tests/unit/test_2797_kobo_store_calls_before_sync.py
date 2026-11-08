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


# With CWNG's answers for profile, subscriptions and deals, the reporter's Kobo
# (firmware 6) got past them and stopped at the next call CWNG still redirected,
# /v1/assets, and its Store and Libby pages stayed empty. Pointed at the store
# directly the same Kobo loads both. So with the proxy on no store call is
# redirected: CWNG asks the store and hands the answer back, as it already did
# for every POST.
@pytest.mark.parametrize("path", [
    "/v1/assets",
    "/v1/products/dailydeal",
    "/v1/products/featured/",
    "/v1/products/books/external/od_1,od_2",
    "/v1/user/recommendations",
    "/v1/user/loyalty/benefits",
])
def test_with_the_proxy_on_no_store_call_is_redirected(kobo_app, store, path):
    store.reply = store_reply(200, b'{"from": "store"}')

    response = ask(kobo_app, path)

    assert response.status_code == 200, (response.status_code, response.headers.get("Location"))
    assert response.get_json() == {"from": "store"}
    assert store.calls[0].url == "https://storeapi.kobo.com" + path
    assert store.calls[0].method == "GET"
    assert store.calls[0].headers.get("Authorization") == "Bearer device-session"


def test_a_store_query_string_reaches_the_store(kobo_app, store):
    store.reply = store_reply(200, b'{"from": "store"}')

    response = ask(kobo_app, "/v1/products/featured/?PageIndex=2&PageSize=10")

    assert response.status_code == 200
    assert store.calls[0].url == (
        "https://storeapi.kobo.com/v1/products/featured/?PageIndex=2&PageSize=10")


def test_a_refused_assets_call_gets_the_proxy_off_answer(kobo_app, store):
    # /v1/assets comes between Init and the library sync, like profile and deals.
    store.reply = store_reply(
        401, b'{"ResponseStatus":{"ErrorCode":"UnauthorizedAccessException"}}')

    response = ask(kobo_app, "/v1/assets")

    assert response.status_code == 200
    assert response.get_json() == {}


def test_an_unreachable_store_answers_a_browsing_call_with_an_error_not_a_crash(kobo_app, store):
    store.raises = requests.ConnectTimeout("slow")

    response = ask(kobo_app, "/v1/products/featured/")

    assert response.status_code == 504


def test_a_proxied_store_answer_is_not_written_to_the_log(kobo_app, store):
    # These answers were redirected before, so they never reached the log, and
    # people paste DEBUG logs into public issues.
    store.reply = store_reply(200, b'{"Wishlist": ["reader@private.invalid"]}')

    response = ask(kobo_app, "/v1/user/wishlist")

    assert response.get_json() == {"Wishlist": ["reader@private.invalid"]}
    assert "reader@private.invalid" not in "\n".join(store.logged)
