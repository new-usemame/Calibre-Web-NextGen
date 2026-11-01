# SPDX-License-Identifier: GPL-3.0-or-later
"""A Kobo being paired asks the server to add it before its first library sync (D#2781).

Nickel queues ``AddDeviceCommand`` ahead of ``SyncLibraryCommand``; a 404 on
``POST /v1/user/add-device`` cancels the whole queue, so the device stops
right after ``/v1/initialization`` and never asks for ``/v1/library/sync``
(device log in janeczku/calibre-web#2477). ``/v1/auth/refresh`` is the
device's token refresh and fails the same way.

These drive the real Kobo blueprint with a real auth token row.
"""

from types import SimpleNamespace

import pytest
from flask import Flask, make_response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

HEADERS = {"x-kobo-deviceid": "b" * 64, "x-kobo-devicemodel": "Kobo Libra Colour"}


@pytest.fixture
def kobo_app(tmp_path, monkeypatch):
    from cps import constants, kobo, kobo_auth, ub
    from cps.cw_login import LoginManager
    from cps.usermanagement import load_user

    engine = create_engine(f"sqlite:///{tmp_path / 'app.sqlite'}")
    ub.Base.metadata.create_all(engine)
    database = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", database)
    monkeypatch.setattr(ub, "session_commit", lambda: database.commit() or True)
    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(kobo_auth, "limiter", SimpleNamespace(check=lambda: None, current_limits=[]))
    monkeypatch.setattr(kobo.config, "config_kobo_sync", True, raising=False)
    monkeypatch.setattr(kobo.config, "config_kobo_proxy", False, raising=False)
    monkeypatch.setattr(kobo.config, "config_allow_reverse_proxy_header_login", False, raising=False)

    user = ub.User(name="reader", email="reader@example.invalid", password="unused", role=0)
    database.add(user)
    database.flush()
    token = ub.RemoteAuthToken()
    token.user_id = user.id
    token.auth_token = "reader-token"
    token.token_type = 1
    database.add(token)
    database.commit()

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-session-secret")
    LoginManager(app).user_loader(load_user)
    app.register_blueprint(kobo.kobo)
    yield SimpleNamespace(app=app, kobo=kobo)
    database.close()
    engine.dispose()


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

    monkeypatch.setattr(kobo_app.kobo, "redirect_or_proxy_request", store)
    response = kobo_app.app.test_client().post(
        "/kobo/reader-token/v1/user/add-device", headers=HEADERS, json=ADD_DEVICE_BODY)

    assert forwarded == [True]
    assert response.get_json() == {"from": "store"}


def test_an_unknown_token_is_still_refused(kobo_app):
    response = kobo_app.app.test_client().post(
        "/kobo/not-a-token/v1/user/add-device", headers=HEADERS, json=ADD_DEVICE_BODY)

    assert response.status_code in (401, 403)
