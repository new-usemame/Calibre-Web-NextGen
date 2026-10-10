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
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            session = self.headers.get('X-Transmission-Session-Id')
            self.send_response(200 if session == 'owned-session' else 409)
            self.send_header('X-Transmission-Session-Id', 'owned-session')
            self.send_header('Set-Cookie', 'SID=owned; HttpOnly')
            self.send_header('Authorization', 'must-not-return')
            self.end_headers()
            if session == 'owned-session': self.wfile.write(body)
        def do_GET(self):
            try:
                if self.path == '/magnet':
                    self.send_response(302)
                    self.send_header('Location', 'magnet:?xt=urn:btih:'+'a'*40)
                    self.end_headers()
                    return
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


def test_indexer_descriptor_redirect_never_forwards_its_original_api_key():
    server = Server([Reply(status=301, headers={'Location': 'https://source.example/nzb?apikey=source-key'}), Reply(b'<nzb/>')])
    policy = http.HTTPPolicy(query_secrets=('PROWLARR_SECRET',))
    assert http.fetch_document('https://prowlarr.example/download?apikey=PROWLARR_SECRET', policy, session_factory=server).body == b'<nzb/>'
    assert 'PROWLARR_SECRET' not in server.calls[1][0]
    malicious = Server([Reply(status=302, headers={'Location': 'https://foreign.example/nzb?x=PROWLARR%5FSECRET'})])
    with pytest.raises(http.TransportError, match='credentials_redirected'):
        http.fetch_document('https://prowlarr.example/download?apikey=PROWLARR_SECRET', policy, session_factory=malicious)
    assert len(malicious.calls) == 1


def test_multipart_submission_cannot_redirect_keys_or_descriptor_bytes():
    class UploadServer(Server):
        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return self.replies.pop(0)
    server = UploadServer([Reply(status=307, headers={'Location': 'https://foreign.example/api'})])
    with pytest.raises(http.TransportError, match='redirect_limit'):
        http.fetch_document('https://sab.example/api', http.HTTPPolicy(), session_factory=server,
            form={'mode': 'addfile', 'apikey': 'SAB_SECRET'}, upload=('owned.nzb', b'<nzb/>'))
    assert len(server.calls) == 1
    assert server.calls[0][1]['files']['name'][1] == b'<nzb/>'

@pytest.mark.parametrize('size', [10000, 60000, 200000, 500000])
def test_slow_child_start_consumes_entire_large_nzb_before_post(monkeypatch, size):
    import subprocess
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    descriptor = b'<nzb><file>' + b'x' * size + b'</file></nzb>'
    received = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":true,"nzo_ids":["owned"]}')
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = subprocess.Popen
    def slow_start(args, **kwargs):
        args = list(args)
        args[2] = 'import time; time.sleep(0.5); ' + args[2]
        return original(args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', slow_start)
    url = f'http://127.0.0.1:{server.server_port}/api'
    try:
        result = http.run_transfer(url, local_policy(url, 2),
            form={'mode': 'addfile', 'apikey': 'test-key'}, upload=('owned.nzb', descriptor))
        assert b'"status":true' in result.body
        assert len(received) == 1 and descriptor in received[0]
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_real_child_raw_rpc_session_challenge_and_empty_body(real_source):
    from dataclasses import replace
    policy = replace(local_policy(real_source), credential_origins=(real_source,))
    request = b'{"method":"session-get","arguments":{}}'
    challenge = http.run_transfer(real_source, policy, body=request, accepted_statuses=(200,409), accept_empty=True)
    assert challenge.status == 409 and challenge.body == b''
    assert challenge.headers == {'x-transmission-session-id':'owned-session', 'set-cookie':'SID=owned; HttpOnly'}
    result = http.run_transfer(real_source, policy, body=request, headers={'Content-Type':'application/json', 'X-Transmission-Session-Id':challenge.headers['x-transmission-session-id']})
    assert result.status == 200 and result.body == request
    with pytest.raises(http.TransportError, match='credentials_redirected'):
        http.run_transfer(real_source, local_policy(real_source), body=request, headers={'Cookie':'SID=owned'})


def test_descriptor_only_magnet_redirect_is_terminal_and_never_fetched():
    magnet='magnet:?xt=urn:btih:'+'a'*40+'&tr=https%3A%2F%2Ftracker.example%2Fannounce'
    server=Server([Reply(status=302,headers={'Location':magnet})])
    policy=http.HTTPPolicy(authorization='Bearer PRIVATE',credential_origins=('https://indexer.example',),query_secrets=('PRIVATE',))
    from dataclasses import replace
    doc=http.fetch_document('https://indexer.example/download?apikey=PRIVATE',replace(policy,allow_magnet_redirect=True),session_factory=server)
    assert doc.url==magnet and doc.body==b'' and len(server.calls)==1
    assert 'PRIVATE' not in repr(doc)
    with pytest.raises(http.TransportError):
        http.fetch_document('https://indexer.example/book',policy,session_factory=Server([Reply(status=302,headers={'Location':magnet})]))


@pytest.mark.parametrize('location',['magnet:?xt=urn:btih:'+'a'*40+'&dn=%2550%2552%2549%2556%2541%2554%2545','magnet:'+('x'*8193),'magnet:?xt=urn:btih:'+'a'*40+'\x7f'])
def test_magnet_redirect_rejects_source_secret_and_unbounded_uri(location):
    server=Server([Reply(status=302,headers={'Location':location})])
    with pytest.raises(http.TransportError):
        http.fetch_document('https://indexer.example/download',http.HTTPPolicy(allow_magnet_redirect=True,query_secrets=('PRIVATE',)),session_factory=server)
    assert len(server.calls)==1


def test_real_child_returns_descriptor_magnet_without_fetching(real_source,tmp_path):
    from dataclasses import replace
    policy=replace(local_policy(real_source),allow_magnet_redirect=True)
    doc=http.run_transfer(real_source+'/magnet',policy)
    assert doc.url=='magnet:?xt=urn:btih:'+'a'*40 and doc.body==b''
    with pytest.raises(http.TransportError):
        http.download_file(real_source+'/magnet',policy,tmp_path/'book',max_bytes=1000)
    assert not (tmp_path/'book').exists()


@pytest.mark.parametrize('secret,encoded',[('PRIVATE KEY','PRIVATE+KEY'),('PRIVATE+KEY','PRIVATE%2BKEY'),('PRIVATE KEY','PRIVATE%2520KEY')])
def test_magnet_redirect_checks_literal_and_form_decoded_source_secrets(secret,encoded):
    location='magnet:?xt=urn:btih:'+'a'*40+'&dn='+encoded
    server=Server([Reply(status=302,headers={'Location':location})])
    with pytest.raises(http.TransportError,match='credentials_redirected'):
        http.fetch_document('https://indexer.example/download',http.HTTPPolicy(allow_magnet_redirect=True,query_secrets=(secret,)),session_factory=server)
    assert len(server.calls)==1
