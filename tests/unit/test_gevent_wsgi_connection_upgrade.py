# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""A request body must reach the app even when a proxy sends ``Connection: upgrade``.

Issue new-usemame/Calibre-Web-NextGen#2838: behind two nginx proxies that set
``proxy_set_header Connection "upgrade"`` on every request (a common WebSocket
snippet), creating an app password and signing in took 90 seconds and ended in a
504, while bodiless requests were instant. gevent treats any request whose
``Connection`` header is ``upgrade`` as a protocol switch and hands the app the
raw socket file instead of a stream bounded by ``Content-Length``, so reading
the body waits for bytes that never come. CWNG serves no upgrade routes and
closes every connection after one response, so the body must be read normally.
"""


import pytest

pytest.importorskip("gevent")

import gevent  # noqa: E402
from gevent.pywsgi import WSGIServer  # noqa: E402
from werkzeug.wrappers import Request  # noqa: E402

from cps.gevent_wsgi import MyWSGIHandler  # noqa: E402


def _echo_form_app(environ, start_response):
    # Werkzeug is what Flask uses to read form and JSON bodies.
    request = Request(environ)
    body = ("name=" + request.form.get("name", "<missing>")).encode()
    start_response("201 Created", [("Content-Type", "text/plain"),
                                   ("Content-Length", str(len(body)))])
    return [body]


def _post(port, connection_header, extra_headers=""):
    body = b"name=reader-app"
    raw = (
        "POST /api/v1/account/app-passwords HTTP/1.1\r\n"
        "Host: books.example\r\n"
        f"Connection: {connection_header}\r\n"
        f"{extra_headers}"
        "Content-Type: application/x-www-form-urlencoded\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode() + body
    sock = gevent.socket.create_connection(("127.0.0.1", port))
    try:
        sock.sendall(raw)
        chunks = []
        with gevent.Timeout(5, False):
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
        return b"".join(chunks)
    finally:
        sock.close()


@pytest.fixture
def server():
    srv = WSGIServer(("127.0.0.1", 0), _echo_form_app, handler_class=MyWSGIHandler, log=None)
    srv.start()
    try:
        yield srv
    finally:
        srv.stop(timeout=1)


@pytest.mark.parametrize("connection_header", ["upgrade", "Upgrade", "keep-alive"])
def test_post_body_is_read_whatever_the_proxy_puts_in_connection(server, connection_header):
    response = _post(server.server_port, connection_header)

    assert response.startswith(b"HTTP/1.1 201"), response[:200]
    assert response.endswith(b"name=reader-app")
    # The forced-close policy (fork #193) still holds on these responses.
    assert b"\r\nConnection: close\r\n" in response


def test_post_with_upgrade_websocket_headers_is_still_read_as_a_normal_request(server):
    # Browsers never send this to CWNG, but a proxy that forwards both headers
    # must not make the app wait on the socket either: CWNG has no 101 routes.
    response = _post(server.server_port, "Upgrade", extra_headers="Upgrade: websocket\r\n")

    assert response.startswith(b"HTTP/1.1 201"), response[:200]
    assert response.endswith(b"name=reader-app")
