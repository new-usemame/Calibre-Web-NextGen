# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression tests for ``cps.gevent_wsgi.MyWSGIHandler``.

Issue new-usemame/Calibre-Web-NextGen#147: gevent calls ``format_request``
from its access-log path even for requests that never parsed as HTTP
(e.g. a TLS ClientHello arriving on a plain-HTTP listener — bytes
``\\x16\\x03\\x01...``). In that case ``get_environ`` is never invoked,
so ``self.environ`` is ``None`` and our override raised
``AttributeError: 'NoneType' object has no attribute 'get'`` before
producing the access-log line. The greenlet died on every such request.
"""

from types import SimpleNamespace

import pytest

pytest.importorskip("gevent")

from gevent import socket  # noqa: E402
from gevent.pywsgi import WSGIServer  # noqa: E402

from cps.gevent_wsgi import MyWSGIHandler  # noqa: E402


def _stub(**overrides):
    """Build a SimpleNamespace with every attribute ``format_request`` reads.

    We bypass ``MyWSGIHandler.__init__`` (which needs a real socket) and
    invoke the unbound method against the stub. Test caller overrides
    only the fields it cares about.
    """
    base = dict(
        time_start=0.0,
        time_finish=0.0,
        response_length=None,
        environ=None,
        client_address=("::1", 12345),
        requestline=None,
        _orig_status=None,
        status=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_format_request_does_not_crash_when_environ_is_none():
    handler = _stub(environ=None, requestline=None, status="400 Bad Request")
    result = MyWSGIHandler.format_request(handler)
    assert isinstance(result, str)
    assert "400" in result


def test_format_request_uses_forwarded_for_when_present():
    handler = _stub(
        environ={"HTTP_X_FORWARDED_FOR": "203.0.113.7"},
        client_address=("::1", 12345),
        requestline="GET / HTTP/1.1",
        _orig_status="200 OK",
        response_length=42,
    )
    result = MyWSGIHandler.format_request(handler)
    assert "203.0.113.7" in result


def test_format_request_falls_back_to_client_address_without_forwarded_for():
    handler = _stub(
        environ={},
        client_address=("198.51.100.4", 12345),
        requestline="GET / HTTP/1.1",
        _orig_status="200 OK",
        response_length=42,
    )
    result = MyWSGIHandler.format_request(handler)
    assert "198.51.100.4" in result


def test_format_request_handles_none_client_address_with_none_environ():
    handler = _stub(environ=None, client_address=None, status="400 Bad Request")
    result = MyWSGIHandler.format_request(handler)
    assert isinstance(result, str)
    assert result.startswith("- ")


def test_read_request_forces_connection_close():
    """``MyWSGIHandler.read_request`` must set ``close_connection = True``
    on every parsed request.

    Reverse-proxy keepalive sockets can stay attached to the gevent
    process after the client side has gone away — when the app is
    overloaded or restarted, those stale sockets prevent the gevent
    process from accepting new work, and the healthcheck wedges because
    no greenlet can run. Forcing connection-close after each response
    means the proxy renegotiates a fresh socket on the next request,
    which avoids the stuck-keepalive failure mode.

    Backport of CWA #1335 by @I-Would-Like-To-Report-A-Bug-Please. Pins
    the source-level invariant so a future refactor (or upstream PR
    pulling the underlying ``WSGIHandler`` apart) can't silently revert
    the close-after-response behaviour.
    """
    import inspect

    source = inspect.getsource(MyWSGIHandler)
    assert "def read_request" in source, (
        "MyWSGIHandler must override read_request to force "
        "self.close_connection = True after each parsed request."
    )
    assert "self.close_connection = True" in source, (
        "MyWSGIHandler.read_request must set self.close_connection = True "
        "so stale reverse-proxy keepalive sockets don't accumulate against "
        "the gevent process. See fork issue #193 + CWA #1335."
    )


def _request_real_server(request_version, app_connection=None):
    """Return one response from a real MyWSGIHandler-backed TCP server."""
    body = b"ok"

    def application(_environ, start_response):
        headers = [
            ("Content-Type", "text/plain"),
            ("Content-Length", str(len(body))),
        ]
        if app_connection is not None:
            headers.append(("Connection", app_connection))
        start_response("200 OK", headers)
        return [body]

    server = WSGIServer(
        ("127.0.0.1", 0),
        application,
        handler_class=MyWSGIHandler,
        log=None,
    )
    server.start()
    client = socket.create_connection(server.address, timeout=2)
    try:
        request = (
            f"GET / {request_version}\r\n"
            "Host: localhost\r\n"
            "\r\n"
        ).encode("ascii")
        client.sendall(request)

        response = bytearray()
        while chunk := client.recv(4096):
            response.extend(chunk)
    finally:
        client.close()
        server.stop(timeout=1)

    head, separator, _response_body = bytes(response).partition(b"\r\n\r\n")
    assert separator, response
    return head.split(b"\r\n")[1:]


def _connection_values(response_headers):
    return [
        value.strip().lower()
        for name, separator, value in (header.partition(b":") for header in response_headers)
        if separator and name.strip().lower() == b"connection"
    ]


def test_http_1_1_response_advertises_connection_close():
    headers = _request_real_server("HTTP/1.1")
    assert _connection_values(headers) == [b"close"]


def test_http_1_0_response_advertises_connection_close_once():
    headers = _request_real_server("HTTP/1.0")
    assert _connection_values(headers) == [b"close"]


@pytest.mark.parametrize("app_connection", ["close", "keep-alive"])
def test_app_connection_header_is_normalized_to_one_close_header(app_connection):
    headers = _request_real_server("HTTP/1.1", app_connection=app_connection)
    assert _connection_values(headers) == [b"close"]
