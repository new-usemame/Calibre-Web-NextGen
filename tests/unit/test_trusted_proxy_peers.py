# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reverse-proxy headers are believed only from a peer on a trusted network.

A reverse proxy tells the app who the client is (X-Forwarded-For), how the
browser reached it (X-Forwarded-Proto, X-Forwarded-Host) and where it is
mounted (X-Forwarded-Prefix, X-Script-Name). A client that reaches the
listener directly is not a proxy, so the headers are honoured only
when the connection itself comes from where a proxy sits: this host, the
docker network, the LAN, a tailnet. Anyone else is taken at their own
address and scheme.

Requests go through the app's own middleware stack in its production order.
"""

import logging

import flask
import pytest
from werkzeug.middleware.proxy_fix import ProxyFix

from cps.reverseproxy import (ReverseProxied, TrustedProxyPeers,
                              parse_trusted_networks)

pytestmark = pytest.mark.unit

PROXY_HEADERS_SENT = {
    "X-Forwarded-For": "127.0.0.1",
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": "books.example.com",
}


def _client(networks):
    app = flask.Flask(__name__)

    @app.route("/who")
    def who():
        return {"addr": flask.request.remote_addr, "scheme": flask.request.scheme,
                "host": flask.request.host}

    inner = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.wsgi_app = TrustedProxyPeers(ReverseProxied(inner), networks)
    return app.test_client()


def _who(peer, headers=PROXY_HEADERS_SENT, networks=parse_trusted_networks(None)):
    response = _client(networks).get("/who", headers=headers,
                                     environ_base={"REMOTE_ADDR": peer})
    return response.get_json()


@pytest.mark.parametrize("peer", ["198.51.100.23", "2001:db8::17", "8.8.8.8"])
def test_a_client_on_a_public_address_is_taken_at_its_own_address(peer):
    assert _who(peer) == {"addr": peer, "scheme": "http", "host": "localhost"}


@pytest.mark.parametrize("peer", [
    "127.0.0.1",            # a proxy on the same host
    "172.18.0.2",           # the docker network
    "192.168.1.10",         # the LAN
    "10.8.0.4",
    "100.101.102.103",      # a tailnet
    "fd12:3456::1",         # IPv6 private
    "::1",
    "::ffff:192.168.1.10",  # an IPv4 LAN peer on a dual-stack socket
])
def test_a_proxy_where_proxies_sit_is_believed(peer):
    assert _who(peer) == {"addr": "127.0.0.1", "scheme": "https", "host": "books.example.com"}


EVERY_PROXY_HEADER = dict(PROXY_HEADERS_SENT, **{
    "X-Forwarded-Port": "8443", "X-Forwarded-Prefix": "/elsewhere",
    "Forwarded": "for=127.0.0.1;proto=https", "X-Scheme": "https",
    "X-Script-Name": "/elsewhere", "X-Real-IP": "127.0.0.1"})


def test_no_proxy_header_from_a_direct_client_reaches_the_app():
    app = flask.Flask(__name__)

    @app.route("/<path:anything>")
    def echo(anything):
        environ = flask.request.environ
        return {"proxy_headers": sorted(k for k in environ if k.startswith("HTTP_X_")
                                        or k == "HTTP_FORWARDED"),
                "script_name": environ.get("SCRIPT_NAME", ""),
                "scheme": flask.request.scheme, "addr": flask.request.remote_addr}

    inner = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.wsgi_app = TrustedProxyPeers(ReverseProxied(inner), parse_trusted_networks(None))
    seen = app.test_client().get("/elsewhere/page", headers=EVERY_PROXY_HEADER,
                                 environ_base={"REMOTE_ADDR": "198.51.100.23"}).get_json()
    assert seen == {"proxy_headers": [], "script_name": "", "scheme": "http",
                    "addr": "198.51.100.23"}


@pytest.mark.parametrize("peer", ["", "0.0.0.0"])
def test_a_unix_socket_peer_is_this_host(peer):
    # gevent reports no address for a Unix-socket client, tornado 0.0.0.0.
    assert _who(peer)["scheme"] == "https"


def test_private_keeps_the_default_networks_in_a_list():
    networks = parse_trusted_networks("private, 203.0.113.7")
    assert _who("203.0.113.7", networks=networks)["scheme"] == "https"
    assert _who("172.18.0.2", networks=networks)["scheme"] == "https"
    assert _who("198.51.100.23", networks=networks)["scheme"] == "http"


@pytest.mark.parametrize("value", ["", "localhost nginx"])
def test_a_list_with_nothing_usable_means_the_default(value):
    assert parse_trusted_networks(value) == parse_trusted_networks(None)


def test_the_setting_replaces_the_default_networks():
    networks = parse_trusted_networks("203.0.113.7, 10.0.0.0/8")
    assert _who("203.0.113.7", networks=networks)["scheme"] == "https"
    assert _who("10.1.2.3", networks=networks)["scheme"] == "https"
    assert _who("192.168.1.10", networks=networks)["scheme"] == "http"


def test_a_star_trusts_every_peer_as_before():
    assert _who("198.51.100.23", networks=parse_trusted_networks("*"))["addr"] == "127.0.0.1"


def test_an_unreadable_entry_is_reported_and_the_rest_still_apply(caplog):
    networks = parse_trusted_networks("proxy.lan 10.0.0.0/8")
    assert "'proxy.lan' is not an address or network" in caplog.text
    assert _who("10.1.2.3", networks=networks)["scheme"] == "https"


def test_ignored_headers_are_logged_once_per_peer(caplog):
    client = _client(parse_trusted_networks(None))
    caplog.set_level(logging.WARNING)
    for _ in range(3):
        client.get("/who", headers=PROXY_HEADERS_SENT, environ_base={"REMOTE_ADDR": "198.51.100.23"})
    client.get("/who", environ_base={"REMOTE_ADDR": "198.51.100.99"})  # sent no proxy headers
    warnings = [r.getMessage() for r in caplog.records if "Ignoring reverse-proxy" in r.getMessage()]
    assert len(warnings) == 1 and "198.51.100.23" in warnings[0]


def test_only_a_believed_proxy_marks_the_app_as_proxied():
    """Kobo builds its URLs from current_app.wsgi_app.is_proxied."""
    app_client = _client(parse_trusted_networks(None))
    gate = app_client.application.wsgi_app
    app_client.get("/who", headers=PROXY_HEADERS_SENT, environ_base={"REMOTE_ADDR": "198.51.100.23"})
    assert gate.is_proxied is False
    app_client.get("/who", headers=PROXY_HEADERS_SENT, environ_base={"REMOTE_ADDR": "172.18.0.2"})
    assert gate.is_proxied is True
