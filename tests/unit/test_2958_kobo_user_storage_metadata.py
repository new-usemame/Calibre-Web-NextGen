# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression for the Kobo Libra Colour Metadata read after local OAuth."""

from urllib.parse import urlsplit

from cps import readingservices
from .kobo_pairing_world import HEADERS, kobo_app  # noqa: F401


def _client_and_headers(kobo_app):
    kobo_app.app.register_blueprint(readingservices.readingservices_userstorage)
    oauth = kobo_app.app.test_client().post("/kobo/reader-token/oauth/token", json={}, headers=HEADERS)
    assert oauth.status_code == 200
    headers = {**HEADERS, "Authorization": f"Bearer {oauth.json['access_token']}"}
    # Reading Services may be called without the device handshake's session cookie.
    return kobo_app.app.test_client(use_cookies=False), headers


def test_proxy_off_metadata_read_is_local_after_oauth(kobo_app, requests_mock):
    client, headers = _client_and_headers(kobo_app)
    requests_mock.get(
        f"{readingservices.KOBO_READING_SERVICES_URL}/api/UserStorage/Metadata",
        status_code=401,
    )

    response = client.get("/api/UserStorage/Metadata", headers=headers)

    assert response.status_code == 200
    assert response.json == {}
    assert response.cache_control.no_store
    assert requests_mock.call_count == 0


def test_only_proxy_off_sync_metadata_reads_are_answered_locally(kobo_app, monkeypatch, requests_mock):
    client, headers = _client_and_headers(kobo_app)
    for sync, proxy, method, path in (
        (True, True, "GET", "/api/UserStorage/Metadata"),
        (False, False, "GET", "/api/UserStorage/Metadata"),
        (True, False, "GET", "/api/UserStorage/Other"),
        (True, False, "POST", "/api/UserStorage/Metadata"),
    ):
        monkeypatch.setattr(readingservices.config, "config_kobo_sync", sync)
        monkeypatch.setattr(readingservices.config, "config_kobo_proxy", proxy)
        requests_mock.register_uri(
            method, f"{readingservices.KOBO_READING_SERVICES_URL}{path}",
            json={"upstream": True}, status_code=202,
            headers={"Content-Type": "application/json"},
        )
        response = client.open(path, method=method, headers=headers)
        assert (response.status_code, response.json) == (202, {"upstream": True})
        upstream = requests_mock.last_request
        assert (upstream.method, urlsplit(upstream.url).path) == (method, path)
        assert upstream.headers["Authorization"] == headers["Authorization"]
    assert requests_mock.call_count == 4
