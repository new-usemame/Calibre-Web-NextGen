# SPDX-License-Identifier: GPL-3.0-or-later
"""A store call CWNG has no route for still reaches the store (#2962).

The Kobo sends its storeapi.kobo.com calls to CWNG. After Kobo moved Libby's
library cards to the account, Libby showed "Network Error": a store path CWNG
had no route for was answered with Flask's 404 instead of the store's answer.
The paths below stand for any such call; Kobo can add more at any time.

These drive the real Kobo blueprint and stand in only for the store's HTTP reply.
"""

import pytest
import requests

from .kobo_pairing_world import kobo_app  # noqa: F401
from .test_2797_kobo_store_calls_before_sync import AUTH, ask, store, store_reply  # noqa: F401

UNKNOWN_STORE_CALLS = [
    "/v1/user/librarycards",
    "/v1/user/library/cards/some-card-id",
    "/v2/users/personalizedrecommendations",
]


@pytest.mark.parametrize("path", UNKNOWN_STORE_CALLS)
@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
def test_with_the_proxy_on_an_unknown_store_call_gets_the_stores_answer(
        kobo_app, store, path, method):
    store.reply = store_reply(200, b'{"from": "store"}')

    response = kobo_app.app.test_client().open(
        "/kobo/reader-token" + path, method=method, headers=AUTH, json={})

    assert response.status_code == 200, response.status_code
    assert response.get_json() == {"from": "store"}
    assert store.calls[0].url == "https://storeapi.kobo.com" + path
    assert store.calls[0].method == method
    assert store.calls[0].headers.get("Authorization") == "Bearer device-session"


def test_with_the_proxy_off_an_unknown_store_call_is_answered_empty(kobo_app, store, monkeypatch):
    monkeypatch.setattr(kobo_app.kobo.config, "config_kobo_proxy", False, raising=False)

    response = ask(kobo_app, "/v1/user/librarycards")

    assert response.status_code == 200
    assert response.get_json() == {}
    assert store.calls == []


def test_an_unreachable_store_answers_an_unknown_call_with_a_gateway_error(kobo_app, store):
    store.raises = requests.ConnectTimeout("slow")

    response = ask(kobo_app, "/v1/user/librarycards")

    assert response.status_code == 504


def test_the_auth_token_is_not_written_to_the_log(kobo_app, store):
    ask(kobo_app, "/v1/user/librarycards")

    logged = "\n".join(store.logged)
    assert "/v1/user/librarycards" in logged
    assert "reader-token" not in logged


@pytest.mark.parametrize("path, endpoint", [
    ("/v1/library/sync", "kobo.HandleSyncRequest"),
    ("/v1/initialization", "kobo.HandleInitRequest"),
    ("/v1/user/profile", "kobo.HandleStoreCallBeforeSync"),
    ("/v1/library/some-book-uuid/state", "kobo.HandleStateRequest"),
])
def test_routes_cwng_serves_are_not_taken_by_the_store_fallback(kobo_app, path, endpoint):
    adapter = kobo_app.app.url_map.bind("localhost")

    matched, _ = adapter.match("/kobo/reader-token" + path, method="GET")

    assert matched == endpoint
