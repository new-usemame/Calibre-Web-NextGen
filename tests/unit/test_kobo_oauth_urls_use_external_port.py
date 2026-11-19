# SPDX-License-Identifier: GPL-3.0-or-later
"""The OAuth URLs a Kobo is given must use the same port as its sync URLs (#2975).

With "Proxy unknown requests to Kobo Store" off, ``/v1/initialization`` points
the device's ``oauth_host`` at this server, and the OpenID discovery document
lists the token endpoint. A Kobo sends ``Host`` without a port, so URLs built
from the request alone land on port 80, while ``library_sync`` is built on
``config_external_port``. On one setup the device then asked whatever was on
port 80 (a reverse proxy) for its OpenID configuration, got a 404 on every try,
and stopped right after Init: "sync failed".

These drive the real Kobo blueprint.
"""

import pytest
from flask import Blueprint

from .kobo_pairing_world import HEADERS, kobo_app  # noqa: F401

HOST = "http://192.168.1.2"
EXTERNAL_PORT = 8093


class Fronting:
    """What ``create_app`` wraps around the WSGI app: it knows if a reverse proxy sent the request."""

    def __init__(self, app, is_proxied):
        self.app = app
        self.is_proxied = is_proxied

    def __call__(self, environ, start_response):
        return self.app(environ, start_response)


@pytest.fixture
def world(kobo_app, monkeypatch):
    from cps import kobo

    monkeypatch.setattr(kobo.config, "config_external_port", EXTERNAL_PORT, raising=False)
    monkeypatch.setattr(kobo.config, "config_hardcover_annotations_sync", False, raising=False)
    # HandleInitRequest builds image_host from the site root when a proxy sent the request.
    web = Blueprint("web", __name__)
    web.add_url_rule("/", "index", lambda: "")
    kobo_app.app.register_blueprint(web)

    def ask(path, proxied=False, base_url=HOST):
        kobo_app.app.wsgi_app = Fronting(kobo_app.app.wsgi_app.app if isinstance(
            kobo_app.app.wsgi_app, Fronting) else kobo_app.app.wsgi_app, proxied)
        return kobo_app.app.test_client().get(
            "/kobo/reader-token" + path, headers=HEADERS, base_url=base_url)

    return ask


def test_init_points_oauth_host_at_the_external_port(world):
    resources = world("/v1/initialization").get_json()["Resources"]

    assert resources["library_sync"].startswith(f"{HOST}:{EXTERNAL_PORT}/")
    assert resources["oauth_host"] == f"{HOST}:{EXTERNAL_PORT}/kobo/reader-token/oauth"


def test_the_discovery_document_points_at_the_external_port(world):
    document = world("/oauth/.well-known/openid-configuration").get_json()

    assert document["token_endpoint"] == f"{HOST}:{EXTERNAL_PORT}/kobo/reader-token/oauth/token"
    assert document["issuer"] == f"{HOST}:{EXTERNAL_PORT}/kobo/reader-token"


def test_a_port_in_the_host_header_is_replaced_by_the_external_port(world):
    resources = world("/v1/initialization", base_url=f"{HOST}:8083").get_json()["Resources"]

    assert resources["oauth_host"] == f"{HOST}:{EXTERNAL_PORT}/kobo/reader-token/oauth"


def test_behind_a_reverse_proxy_the_urls_are_left_to_the_proxy(world):
    base_url = "https://books.example.org"
    resources = world("/v1/initialization", proxied=True, base_url=base_url).get_json()["Resources"]
    document = world("/oauth/.well-known/openid-configuration", proxied=True,
                     base_url=base_url).get_json()

    assert resources["oauth_host"] == f"{base_url}/kobo/reader-token/oauth"
    assert document["token_endpoint"] == f"{base_url}/kobo/reader-token/oauth/token"
