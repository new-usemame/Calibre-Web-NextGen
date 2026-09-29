# SPDX-License-Identifier: GPL-3.0-or-later
"""Acquisition transport boundaries, without Flask or a running downloader."""
import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location('_acquisition_http_test_subject', Path(__file__).resolve().parents[2] / 'cps/services/acquisition/http.py')
http = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = http
_SPEC.loader.exec_module(http)


class Reply:
    def __init__(self, body=b'book', status=200, headers=None):
        self.body = body
        self.status_code = status
        self.headers = headers or {}
        self.closed = False
    def __enter__(self):
        return self
    def __exit__(self, *_):
        self.closed = True
    def iter_content(self, chunk_size):
        yield from self.body if isinstance(self.body, list) else [self.body]


class Server:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
    def __call__(self, policy, url):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *_):
        pass
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.replies.pop(0)


def test_redirect_uses_explicit_auth_origins_and_closes_each_response():
    replies = [Reply(status=302, headers={'Location': 'https://cdn.example/book.epub'}), Reply(headers={'Content-Type': 'application/epub+zip'})]
    server = Server(replies)
    policy = http.HTTPPolicy(credential_origins=('https://catalog.example',), authorization='Bearer private-key')
    doc = http.fetch_document('https://catalog.example/get?key=opaque', policy, session_factory=server)
    assert doc.body == b'book'
    assert server.calls[0][1]['headers']['Authorization'] == 'Bearer private-key'
    assert 'Authorization' not in server.calls[1][1]['headers']
    assert all(not options['allow_redirects'] and options['verify'] and options['stream'] for _, options in server.calls)
    assert all(reply.closed for reply in replies)
    assert 'opaque' not in repr(doc) and 'private-key' not in repr(policy)


@pytest.mark.parametrize('url', ['file:///tmp/book', 'http://user:secret@example.org/a', 'https://example.org:0/a', 'http://example.org\\@internal/a', 'https://example.org/\nbook'])
def test_unsafe_url_never_reaches_transport(url):
    server = Server([])
    with pytest.raises(http.TransportError, match='invalid_url'):
        http.fetch_document(url, http.HTTPPolicy(), session_factory=server)
    assert server.calls == []


def test_chunk_limit_and_incomplete_response_remove_owned_partial(tmp_path):
    for reply, code, limit in [(Reply([b'123', b'456']), 'file_too_large', 5),
                               (Reply(b'123', headers={'Content-Length': '4'}), 'incomplete_response', 10),
                               (Reply(b''), 'empty_response', 10)]:
        target = tmp_path / (code + '.part')
        server = Server([reply])
        with pytest.raises(http.TransportError, match=code):
            http.download_file('https://files.example/book', http.HTTPPolicy(), target, max_bytes=limit, session_factory=server)
        assert not target.exists()
        assert reply.closed and len(server.calls) == 1


def test_success_digest_private_permissions_and_existing_file_preservation(tmp_path):
    target = tmp_path / 'owned.part'
    result = http.download_file('https://files.example/book', http.HTTPPolicy(), target, max_bytes=10, session_factory=Server([Reply([b'bo', b'ok'])]))
    assert result.bytes == 4 and result.sha256 == hashlib.sha256(b'book').hexdigest()
    assert target.read_bytes() == b'book' and target.stat().st_mode & 0o777 == 0o600
    server = Server([])
    with pytest.raises(FileExistsError):
        http.download_file('https://files.example/new', http.HTTPPolicy(), target, max_bytes=10, session_factory=server)
    assert target.read_bytes() == b'book' and not server.calls


def test_cancel_checkpoint_aborts_stream_without_leaving_partial(tmp_path):
    target = tmp_path / 'cancel.part'
    calls = 0
    def checkpoint():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError('owned job cancelled')
    reply = Reply([b'first', b'second'])
    with pytest.raises(RuntimeError, match='cancelled'):
        http.download_file('https://files.example/book', http.HTTPPolicy(), target, max_bytes=100, checkpoint=checkpoint, session_factory=Server([reply]))
    assert not target.exists() and reply.closed


def test_retry_after_is_reported_without_retry_or_secret_error(tmp_path):
    server = Server([Reply(status=429, headers={'Retry-After': '60'})])
    with pytest.raises(http.TransportError) as failure:
        http.fetch_document('https://files.example/get?private=secret', http.HTTPPolicy(), session_factory=server)
    assert failure.value.code == 'source_busy' and failure.value.retry_after == 60
    assert 'secret' not in str(failure.value) and len(server.calls) == 1


def test_redirect_loop_and_tls_downgrade_stop_before_second_request():
    for location, code in [('https://files.example/book', 'redirect_loop'), ('http://files.example/book', 'insecure_redirect')]:
        server = Server([Reply(status=302, headers={'Location': location})])
        with pytest.raises(http.TransportError, match=code):
            http.fetch_document('https://files.example/book', http.HTTPPolicy(), session_factory=server)
        assert len(server.calls) == 1


def test_real_requests_session_does_not_buffer_redirect_body(monkeypatch):
    import types
    import requests
    root = Path(__file__).resolve().parents[2]
    # Load real Advocate under an isolated package without starting the Flask app.
    for name, path in [('_acquisition_real', root / 'cps'),
                       ('_acquisition_real.services', root / 'cps/services'),
                       ('_acquisition_real.services.acquisition', root / 'cps/services/acquisition')]:
        package = types.ModuleType(name)
        package.__path__ = [str(path)]
        monkeypatch.setitem(sys.modules, name, package)
    name = '_acquisition_real.services.acquisition.http'
    spec = importlib.util.spec_from_file_location(name, root / 'cps/services/acquisition/http.py')
    subject = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, subject)
    spec.loader.exec_module(subject)
    class Body:
        reads = 0
        def stream(self, *args, **kwargs):
            self.reads += 1
            yield b'unbounded redirect data'
        def close(self):
            pass
        def release_conn(self):
            pass
    raw = Body()
    class Adapter(requests.adapters.BaseAdapter):
        def send(self, request, **kwargs):
            response = requests.Response()
            response.status_code = 302
            response.headers['Location'] = 'https://cdn.example/book'
            response.raw = raw
            response.request = request
            response.url = request.url
            return response
        def close(self):
            pass
    with subject._session(subject.HTTPPolicy(), 'https://catalog.example') as session:
        monkeypatch.setattr(session, 'get_adapter', lambda url: Adapter())
        with session.get('https://catalog.example', stream=True, allow_redirects=False) as response:
            assert response.status_code == 302
            assert raw.reads == 0
            assert response.next is None


@pytest.mark.parametrize('limits', [{'deadline': float('nan')}, {'read_timeout': float('inf')}, {'max_redirects': 1.5}, {'connect_timeout': True}])
def test_invalid_resource_limits_are_rejected(limits):
    with pytest.raises(http.TransportError, match='invalid_limits'):
        http.HTTPPolicy(**limits)


def test_malformed_redirect_cannot_echo_secret_in_error():
    server = Server([Reply(status=302, headers={'Location': 'https://private-key：example.org/book'})])
    with pytest.raises(http.TransportError) as failure:
        http.fetch_document('https://catalog.example', http.HTTPPolicy(), session_factory=server)
    assert str(failure.value) == 'invalid_url'


@pytest.fixture
def real_source():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    stop = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            try:
                if self.path == '/headers':
                    stop.wait(10)
                    return
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', '/book')
                    self.send_header('Content-Length', '100000000')
                    self.end_headers()
                    self.wfile.write(b'x')
                    self.wfile.flush()
                    stop.wait(10)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/epub+zip')
                self.send_header('Content-Length', '4' if self.path == '/book' else '100000000')
                self.end_headers()
                if self.path == '/book':
                    self.wfile.write(b'book')
                else:
                    while not stop.wait(0.05):
                        self.wfile.write(b'x')
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port)
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join()


def local_policy(url, deadline=3):
    return http.HTTPPolicy(private_origins=(url,), private_networks=('127.0.0.1/32',), deadline=deadline)


def test_real_child_redirect_skips_large_body_and_publishes_complete_file(real_source, tmp_path):
    target = tmp_path / 'complete.part'
    result = http.run_transfer(real_source + '/redirect', local_policy(real_source), destination=target, max_bytes=10)
    assert target.read_bytes() == b'book' and result.bytes == 4
    assert list(tmp_path.iterdir()) == [target]
    with pytest.raises(FileExistsError):
        http.run_transfer(real_source + '/book', local_policy(real_source), destination=target, max_bytes=10)
    assert target.read_bytes() == b'book' and list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize('path', ['/headers', '/drip'])
def test_hard_child_deadline_handles_headers_and_slow_drip(real_source, tmp_path, path):
    import time
    start = time.monotonic()
    with pytest.raises(http.TransportError, match='transfer_timeout'):
        http.run_transfer(real_source + path, local_policy(real_source, 0.7), destination=tmp_path/'partial', max_bytes=200000000)
    assert time.monotonic() - start < 3
    assert list(tmp_path.iterdir()) == []


def test_real_child_cancellation_and_private_network_denial(real_source, tmp_path):
    with pytest.raises(http.TransportError, match='network_not_allowed'):
        http.run_transfer(real_source + '/book', http.HTTPPolicy(deadline=3))
    calls = 0
    def checkpoint():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        http.run_transfer(real_source+'/headers', local_policy(real_source), destination=tmp_path/'partial', checkpoint=checkpoint)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('broken_symlink', [False, True])
def test_existing_destination_never_starts_quota_bearing_transfer(tmp_path, monkeypatch, broken_symlink):
    import subprocess
    target = tmp_path / 'existing'
    if broken_symlink:
        target.symlink_to(tmp_path / 'absent')
    else:
        target.write_bytes(b'keep')
    def forbidden(*args, **kwargs):
        pytest.fail('Existing destination must fail before starting a network child')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(FileExistsError):
        http.run_transfer('https://files.example/book', http.HTTPPolicy(), destination=target)
    assert target.is_symlink() if broken_symlink else target.read_bytes() == b'keep'



def test_unsolicited_compression_is_rejected_before_decompression():
    class Compressed(Reply):
        def iter_content(self, chunk_size):
            pytest.fail('Must not enter automatic decompression before byte limits')
    reply = Compressed(headers={'Content-Encoding': 'gzip'})
    with pytest.raises(http.TransportError, match='unsupported_content_encoding'):
        http.fetch_document('https://files.example/book', http.HTTPPolicy(), session_factory=Server([reply]))
    assert reply.closed
