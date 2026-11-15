# SPDX-License-Identifier: GPL-3.0-or-later
"""A Kobo being paired asks the server to add it before its first library sync (D#2781).

Nickel queues ``AddDeviceCommand`` ahead of ``SyncLibraryCommand``; a 404 on
``POST /v1/user/add-device`` cancels the whole queue, so the device stops
right after ``/v1/initialization`` and never asks for ``/v1/library/sync``
(device log in janeczku/calibre-web#2477). ``/v1/auth/refresh`` is the
device's token refresh and fails the same way.

These drive the real Kobo blueprint with a real auth token row.
"""

import pytest
from flask import make_response

from .kobo_pairing_world import HEADERS, kobo_app  # noqa: F401


ADD_DEVICE_BODY = {
    "AffiliateName": "Kobo",
    "AppVersion": "4.46.23852",
    "ClientKey": "c2VjcmV0",
    "DeviceId": "b" * 64,
    "PlatformId": "00000000-0000-0000-0000-000000000390",
    "UserKey": "490661bd-1aaa-4ffe-8680-b0a51f103ad8",
}


@pytest.mark.parametrize("path", ["/v1/user/add-device", "/v1/auth/refresh"])
def test_store_proxy_off_answers_the_pairing_calls_itself(kobo_app, path):
    response = kobo_app.app.test_client().post(
        "/kobo/reader-token" + path, headers=HEADERS, json=ADD_DEVICE_BODY)

    assert response.status_code == 200, response.data[:200]
    payload = response.get_json()
    assert payload["TokenType"] == "Bearer"
    assert payload["AccessToken"] and payload["RefreshToken"]
    assert payload["UserKey"] == ADD_DEVICE_BODY["UserKey"]


def test_a_pairing_call_without_a_json_body_is_still_answered(kobo_app):
    response = kobo_app.app.test_client().post(
        "/kobo/reader-token/v1/user/add-device", headers=HEADERS, data=b"")

    assert response.status_code == 200, response.data[:200]
    assert response.get_json()["UserKey"] == ""


def test_store_proxy_on_forwards_add_device_to_the_kobo_store(kobo_app, monkeypatch):
    monkeypatch.setattr(kobo_app.kobo.config, "config_kobo_proxy", True, raising=False)
    forwarded = []

    def store(*_a, **_kw):
        forwarded.append(True)
        return make_response('{"from": "store"}', 200, {"Content-Type": "application/json"})

    monkeypatch.setattr(kobo_app.kobo, "proxy_to_kobo_store", store)
    response = kobo_app.app.test_client().post(
        "/kobo/reader-token/v1/user/add-device", headers=HEADERS, json=ADD_DEVICE_BODY)

    assert forwarded == [True]
    assert response.get_json() == {"from": "store"}


def test_an_unknown_token_is_still_refused(kobo_app):
    response = kobo_app.app.test_client().post(
        "/kobo/not-a-token/v1/user/add-device", headers=HEADERS, json=ADD_DEVICE_BODY)

    assert response.status_code in (401, 403)


def test_a_json_body_that_is_not_an_object_is_answered_not_crashed(kobo_app):
    response = kobo_app.app.test_client().post(
        "/kobo/reader-token/v1/user/add-device", headers=HEADERS, json=[1])

    assert response.status_code == 200, response.data[:200]
    assert response.get_json()["UserKey"] == ""
