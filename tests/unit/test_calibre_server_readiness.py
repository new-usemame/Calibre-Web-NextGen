# SPDX-License-Identifier: GPL-3.0-or-later
"""Probe the HTTP boundary without offering credentials to the listener."""
import importlib.util
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
POLICY = Path(__file__).resolve().parents[2] / "cps" / "calibre_library_target.py"


@pytest.fixture
def policy():
    spec = importlib.util.spec_from_file_location("readiness_policy", POLICY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def peer(*, server="calibre 9.11", status=200, challenge=None, body=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, dict(self.headers)))
            self.send_response_only(status)
            if server is not None:
                self.send_header("Server", server)
            if challenge:
                self.send_header("WWW-Authenticate", challenge)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *_args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port, requests
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.parametrize("server,status,challenge,body,ready", [
    ("other-service", 200, None, {"library_map": {"My_Library": "books"}}, False),
    ("calibre 9.11", 200, None, {"library_map": {"Other": "books"}}, False),
    ("calibre 9.11", 200, None, {"library_map": {"My_Library": "books"}}, True),
    ("calibre 9.11", 200, None, {"library_map": ["My_Library"]}, False),
    ("calibre 9.11", 401, 'Digest realm="calibre", nonce="probe"', None, True),
    (None, 401, 'Digest realm="calibre", nonce="probe", algorithm="MD5", qop="auth"', None, True),
    ("other-service", 401, 'Digest realm="calibre", nonce="probe"', None, False),
    ("calibre 9.11", 401, 'Basic realm="calibre"', None, False),
])
def test_readiness_requires_calibre_protocol_without_credentials(
        policy, server, status, challenge, body, ready):
    with peer(server=server, status=status, challenge=challenge, body=body) as (port, requests):
        assert policy.server_ready("127.0.0.1", port, "/books/My Library/") is ready
    assert requests[0][0] == "/ajax/library-info"
    assert "Authorization" not in requests[0][1]
    assert "Cookie" not in requests[0][1]


def test_ready_unmanaged_peer_cannot_receive_the_configured_password(policy):
    with pytest.raises(RuntimeError, match="without managed ownership"):
        policy.server_target("/books", True, 7777, "127.0.0.1", False,
                             "account", "private-value", lambda *_args: True,
                             lambda *_args: None, lambda: False)
