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

from types import SimpleNamespace

import pytest
from flask import Blueprint, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .kobo_pairing_world import HEADERS, kobo_app  # noqa: F401

HOST = "http://192.168.1.2"
EXTERNAL_PORT = 8093


@pytest.fixture
def world(kobo_app, monkeypatch):
    from cps import kobo
    from cps.reverseproxy import ReverseProxied, TrustedProxyPeers, parse_trusted_networks

    monkeypatch.setattr(kobo.config, "config_external_port", EXTERNAL_PORT, raising=False)
    monkeypatch.setattr(kobo.config, "config_hardcover_annotations_sync", False, raising=False)
    # HandleInitRequest builds image_host from the site root when a proxy sent the request.
    web = Blueprint("web", __name__)
    web.add_url_rule("/", "index", lambda: "")
    kobo_app.app.register_blueprint(web)

    app = kobo_app.app
    app.wsgi_app = TrustedProxyPeers(
        ReverseProxied(ProxyFix(app.wsgi_app, x_host=1, x_proto=1, x_prefix=1)),
        parse_trusted_networks(None),
    )

    def ask(path, *, base_url=HOST, proxy_headers=None):
        return app.test_client().get(
            "/kobo/reader-token" + path,
            headers={**HEADERS, **(proxy_headers or {})},
            base_url=base_url,
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )

    return ask


def test_init_points_oauth_host_at_the_external_port(world):
    resources = world("/v1/initialization").get_json()["Resources"]

    assert resources["library_sync"].startswith(f"{HOST}:{EXTERNAL_PORT}/")
    assert resources["oauth_host"] == f"{HOST}:{EXTERNAL_PORT}/kobo/reader-token/oauth"


def test_the_discovery_document_points_at_the_external_port(world):
    document = world("/oauth/.well-known/openid-configuration").get_json()

    assert document["token_endpoint"] == f"{HOST}:{EXTERNAL_PORT}/kobo/reader-token/oauth/token"
    assert document["issuer"] == f"{HOST}:{EXTERNAL_PORT}/kobo/reader-token"
    assert document["authorization_endpoint"] == f"{document['issuer']}/oauth/authorize"


@pytest.mark.parametrize("host", [HOST, "http://[2001:db8::1]"])
def test_a_port_in_the_host_header_is_replaced_by_the_external_port(world, host):
    resources = world("/v1/initialization", base_url=f"{host}:8083").get_json()["Resources"]
    document = world("/oauth/.well-known/openid-configuration", base_url=f"{host}:8083").get_json()

    assert resources["library_sync"].startswith(f"{host}:{EXTERNAL_PORT}/")
    assert resources["oauth_host"] == f"{host}:{EXTERNAL_PORT}/kobo/reader-token/oauth"
    assert document["token_endpoint"] == f"{host}:{EXTERNAL_PORT}/kobo/reader-token/oauth/token"


def test_discovery_keeps_its_request_origin_with_store_proxy_on(world, monkeypatch):
    from cps import kobo

    monkeypatch.setattr(kobo.config, "config_kobo_proxy", True)
    request_origin = f"{HOST}:8083"
    document = world("/oauth/.well-known/openid-configuration", base_url=request_origin).get_json()

    assert document["token_endpoint"] == f"{request_origin}/kobo/reader-token/oauth/token"


@pytest.mark.parametrize("prefix_header", ["X-Script-Name", "X-Forwarded-Prefix"])
def test_a_reverse_proxy_prefix_keeps_the_public_origin(world, prefix_header):
    public_origin = "https://books.example.org:8443"
    proxy_headers = {prefix_header: "/books"}
    resources = world("/v1/initialization", base_url=public_origin,
                      proxy_headers=proxy_headers).get_json()["Resources"]
    document = world("/oauth/.well-known/openid-configuration", base_url=public_origin,
                     proxy_headers=proxy_headers).get_json()

    base_url = f"{public_origin}/books/kobo/reader-token"
    assert resources["library_sync"].startswith(f"{base_url}/")
    assert resources["oauth_host"] == f"{base_url}/oauth"
    assert document["token_endpoint"] == f"{base_url}/oauth/token"


def test_an_interleaved_direct_request_does_not_change_the_proxy_origin(world, kobo_app):
    @kobo_app.app.before_request
    def serve_direct_request_while_proxied_request_is_active():
        if request.script_root == "/books":
            assert kobo_app.app.test_client().get("/").status_code == 200

    public_origin = "https://books.example.org:8443"
    resources = world("/v1/initialization", base_url=public_origin,
                      proxy_headers={"X-Script-Name": "/books"}).get_json()["Resources"]

    base_url = f"{public_origin}/books/kobo/reader-token"
    assert resources["library_sync"].startswith(f"{base_url}/")
    assert resources["oauth_host"] == f"{base_url}/oauth"


@pytest.mark.parametrize("store_resources", [
    {},
    {"Resources": {"oauth_host": "https://oauth.kobo.com"}},
])
def test_store_proxy_keeps_the_store_oauth_host(world, monkeypatch, store_resources):
    from cps import kobo

    monkeypatch.setattr(kobo.config, "config_kobo_proxy", True)
    monkeypatch.setattr(kobo, "make_request_to_kobo_store",
                        lambda: SimpleNamespace(json=lambda: store_resources))

    resources = world("/v1/initialization").get_json()["Resources"]

    assert resources["oauth_host"] == "https://oauth.kobo.com"
