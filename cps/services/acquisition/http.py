# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded acquisition transport. No retries, ambient credentials or import effects.

Connections are administrator-owned. Private networks are allowed only for exact
configured origins; public catalog links still use the socket-level Advocate
validator. Callers must persist acquisition intent before a quota-bearing GET.

The deadline is cooperative between reads. A production worker must run this
transport in a killable child process to bound DNS, headers and slow-drip bodies;
requests inactivity timeouts alone are not an absolute wall-clock limit.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import math
import os
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote, unquote_plus

import requests


class TransportError(ValueError):
    """Public-safe failure: never contains a URL, response body or credential."""

    def __init__(self, code: str, *, retry_after: int | None = None):
        self.code = code
        self.retry_after = retry_after
        super().__init__(code)


def query_secret_present(value, secrets):
    """Check literal, percent and form-query decoding without forwarding keys."""
    if not secrets: return False
    decoded = value
    for _ in range(8):
        if any(secret in decoded or secret in unquote_plus(decoded) for secret in secrets):
            return True
        next_value = unquote(decoded)
        if next_value == decoded: return False
        decoded = next_value
    return True  # Refuse nested encodings beyond the inspection budget.


def normalized_url(value: str) -> str:
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise TransportError('invalid_url')
    try:
        parts = urlsplit(value)
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError()
        if '\\' in parts.netloc or parts.port == 0:
            raise ValueError()
        parts.hostname.encode('idna')
        return urlunsplit((parts.scheme, parts.netloc, parts.path or '/', parts.query, ''))
    except (ValueError, UnicodeError):
        raise TransportError('invalid_url') from None


def origin(value: str) -> tuple[str, str, int]:
    parts = urlsplit(normalized_url(value))
    return parts.scheme, parts.hostname.encode('idna').decode('ascii').lower(), parts.port or (443 if parts.scheme == 'https' else 80)


@dataclass(frozen=True)
class HTTPPolicy:
    """Explicit trust scope, constructed from validated administrator settings."""
    credential_origins: tuple[str, ...] = field(default=(), repr=False)
    private_origins: tuple[str, ...] = field(default=(), repr=False)
    private_networks: tuple[str, ...] = ()
    authorization: str | None = field(default=None, repr=False)
    query_secrets: tuple[str, ...] = field(default=(), repr=False)
    allow_magnet_redirect: bool = False
    max_redirects: int = 5
    connect_timeout: float = 5.0
    read_timeout: float = 15.0
    deadline: float = 120.0

    def __post_init__(self):
        for value in (*self.credential_origins, *self.private_origins):
            origin(value)
        for value in self.private_networks:
            ipaddress.ip_network(value, strict=True)
        if self.authorization is not None and (len(self.authorization) > 16384 or any(ord(c) < 32 or ord(c) == 127 for c in self.authorization)):
            raise TransportError('invalid_authentication')
        if any(not isinstance(value, str) or not value or len(value) > 16384 for value in self.query_secrets):
            raise TransportError('invalid_authentication')
        timeouts = (self.connect_timeout, self.read_timeout, self.deadline)
        if (type(self.allow_magnet_redirect) is not bool or type(self.max_redirects) is not int or not 0 <= self.max_redirects <= 10
                or any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in timeouts)):
            raise TransportError('invalid_limits')


def authorization(kind: str, secret: str = '', username: str = '') -> str | None:
    if kind == 'none':
        return None
    if not secret or any(c in secret + username for c in '\r\n\x00'):
        raise TransportError('invalid_authentication')
    if kind == 'bearer':
        return 'Bearer ' + secret
    if kind == 'basic' and ':' not in username:
        return 'Basic ' + base64.b64encode((username + ':' + secret).encode('utf-8')).decode('ascii')
    raise TransportError('unsupported_authentication')


@dataclass(frozen=True)
class FetchedDocument:
    body: bytes = field(repr=False)
    url: str = field(repr=False)
    content_type: str
    status: int = 200
    headers: dict = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class DownloadedFile:
    path: Path = field(repr=False)
    bytes: int
    sha256: str
    content_type: str


def _session(policy: HTTPPolicy, url: str):
    # Lazy import keeps parsing/testing independent of Flask bootstrap. Advocate
    # validates the actual resolved socket address rather than resolving twice.
    if __package__:
        from ...cw_advocate import Session, AddrValidator
        from ...cw_advocate.exceptions import AdvocateException
    else:
        # The owned child starts this file directly, without initializing Flask.
        import importlib.util
        import sys
        package_path = Path(__file__).resolve().parents[2] / 'cw_advocate'
        spec = importlib.util.spec_from_file_location('_acquisition_advocate', package_path / '__init__.py', submodule_search_locations=[str(package_path)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = package
        spec.loader.exec_module(package)
        Session, AddrValidator = package.Session, package.AddrValidator
        from _acquisition_advocate.exceptions import AdvocateException
    networks = policy.private_networks if origin(url) in {origin(x) for x in policy.private_origins} else ()
    validator = AddrValidator(ip_whitelist={ipaddress.ip_network(x) for x in networks},
                              port_whitelist={origin(url)[2]}, allow_ipv6=True)
    class AcquisitionSession(Session):
        def resolve_redirects(self, *args, **kwargs):
            # Requests otherwise drains a redirect body to prepare response._next,
            # even with allow_redirects=False. Our caller owns all redirects.
            return iter(())

        def request(self, *args, **kwargs):
            try:
                return super().request(*args, **kwargs)
            except AdvocateException:
                raise TransportError('network_not_allowed') from None

    session = AcquisitionSession(validator=validator)
    session.trust_env = False
    return session


def _retry_after(value: str | None) -> int | None:
    if not value:
        return None
    try:
        seconds = int(value) if value.isdigit() else int(parsedate_to_datetime(value).timestamp() - time.time())
        return min(86400, max(0, seconds))
    except (ValueError, TypeError, OverflowError):
        return None


def _transfer(url, policy, consume, max_bytes, checkpoint, session_factory, *, form=None, upload=None, body=None, headers=None, accepted_statuses=(200,), accept_empty=False, upload_field="name"):
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise TransportError('invalid_limits')
    url = normalized_url(url)
    started = time.monotonic()
    total = 0
    visited = set()
    for hop in range(policy.max_redirects + 1):
        if url in visited:
            raise TransportError('redirect_loop')
        visited.add(url)
        checkpoint()
        if time.monotonic() - started >= policy.deadline:
            raise TransportError('transfer_timeout')
        request_headers = {'Accept-Encoding': 'identity', 'User-Agent': 'Calibre-Web-NextGen'}
        if headers:
            if origin(url) not in {origin(x) for x in policy.credential_origins}:
                raise TransportError('credentials_redirected')
            request_headers.update(headers)
        if policy.authorization and origin(url) in {origin(x) for x in policy.credential_origins}:
            request_headers['Authorization'] = policy.authorization
        try:
            with session_factory(policy, url) as session:
                method = session.post if form is not None or body is not None else session.get
                options = {}
                if form is not None:
                    options['data'] = form
                if body is not None:
                    options['data'] = body
                if upload is not None:
                    options['files'] = {upload_field: (upload[0], upload[1], 'application/x-nzb' if upload_field == 'name' else 'application/x-bittorrent')}
                with method(url, headers=request_headers, allow_redirects=False, stream=True,
                                 timeout=(policy.connect_timeout, policy.read_timeout), verify=True, **options) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        if form is not None or body is not None or headers:
                            raise TransportError('redirect_limit')
                        location = response.headers.get('Location')
                        if not location or hop == policy.max_redirects:
                            raise TransportError('redirect_limit')
                        if policy.allow_magnet_redirect and location.startswith('magnet:'):
                            # Descriptor resolution only: return the URI to the
                            # caller's torrent/tracker validator. Never fetch it.
                            if len(location) > 8192 or any(ord(c) <= 32 or ord(c) == 127 for c in location):
                                raise TransportError('invalid_url')
                            if query_secret_present(location, policy.query_secrets):
                                raise TransportError('credentials_redirected')
                            return location, 0, 'application/x-bittorrent', 200, {}
                        try:
                            target = normalized_url(urljoin(url, location))
                        except ValueError:
                            raise TransportError('invalid_url') from None
                        if origin(target) != origin(url) and query_secret_present(target, policy.query_secrets):
                            raise TransportError('credentials_redirected')
                        if origin(url)[0] == 'https' and origin(target)[0] != 'https':
                            raise TransportError('insecure_redirect')
                        url = target
                        continue
                    if response.status_code in (401, 403) and response.status_code not in accepted_statuses:
                        raise TransportError('needs_auth')
                    if response.status_code in (429, 503):
                        raise TransportError('source_busy', retry_after=_retry_after(response.headers.get('Retry-After')))
                    if response.status_code not in accepted_statuses:
                        raise TransportError('source_http_error')
                    # We negotiate identity. Do not let an unsolicited compressed
                    # body inflate inside Requests before our byte budget runs.
                    if response.headers.get('Content-Encoding', 'identity').strip().lower() not in ('', 'identity'):
                        raise TransportError('unsupported_content_encoding')
                    declared = response.headers.get('Content-Length')
                    if declared is not None:
                        try:
                            declared = int(declared)
                        except ValueError:
                            raise TransportError('invalid_length') from None
                        if declared < 0 or declared > max_bytes:
                            raise TransportError('file_too_large')
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        checkpoint()
                        if time.monotonic() - started >= policy.deadline:
                            raise TransportError('transfer_timeout')
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            raise TransportError('file_too_large')
                        consume(chunk)
                    if not total and not accept_empty:
                        raise TransportError('empty_response')
                    if declared is not None and not response.headers.get('Content-Encoding') and total != declared:
                        raise TransportError('incomplete_response')
                    return url, total, response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower(), response.status_code, {key.lower(): value for key, value in response.headers.items() if key.lower() in ('set-cookie', 'x-transmission-session-id') and len(value) <= 4096}
        except TransportError:
            raise
        except requests.RequestException:
            raise TransportError('source_unreachable') from None
    raise TransportError('redirect_limit')


def fetch_document(url: str, policy: HTTPPolicy, *, max_bytes: int = 2 * 1024 * 1024,
                   checkpoint=lambda: None, session_factory=_session, form=None, upload=None, body=None, headers=None, accepted_statuses=(200,), accept_empty=False, upload_field="name") -> FetchedDocument:
    chunks = []
    final, _, mime, status, returned_headers = _transfer(url, policy, chunks.append, max_bytes, checkpoint, session_factory, form=form, upload=upload, body=body, headers=headers, accepted_statuses=accepted_statuses, accept_empty=accept_empty, upload_field=upload_field)
    return FetchedDocument(b''.join(chunks), final, mime, status, returned_headers)


def download_file(url: str, policy: HTTPPolicy, destination: Path, *, max_bytes: int,
                  checkpoint=lambda: None, session_factory=_session) -> DownloadedFile:
    """Write only a new owned staging path. Never replace a file or publish to ingest."""
    if policy.allow_magnet_redirect:
        raise TransportError('invalid_configuration')
    digest = hashlib.sha256()
    created = False
    try:
        with destination.open('xb') as output:
            created = True
            os.chmod(destination, 0o600)
            def consume(chunk):
                output.write(chunk)
                digest.update(chunk)
            _, count, mime, _, _ = _transfer(url, policy, consume, max_bytes, checkpoint, session_factory)
            output.flush()
            os.fsync(output.fileno())
        return DownloadedFile(destination, count, digest.hexdigest(), mime)
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def run_transfer(url: str, policy: HTTPPolicy, *, destination: Path | None = None,
                 max_bytes: int = 2 * 1024 * 1024, checkpoint=lambda: None, form=None, upload=None, body=None, headers=None, accepted_statuses=(200,), accept_empty=False, upload_field="name"):
    """Production entry point: hard deadline/cancellation around owned child I/O.

    A file is first written in a private temporary directory. Only successful
    completion publishes it, with exclusive link creation, to caller-owned staging.
    Neither this function nor its child publishes into the ingest watch folder.
    """
    import json
    import subprocess
    import sys
    import tempfile
    from dataclasses import asdict
    normalized_url(url)
    if form is not None and (destination is not None or not isinstance(form, dict)
            or len(form) > 32 or any(not isinstance(k, str) or not isinstance(v, str)
                or len(k) > 128 or len(v) > 16384 for k, v in form.items())):
        raise TransportError('invalid_configuration')
    if upload is not None and (form is None or len(upload) != 2 or not isinstance(upload[0], str)
            or len(upload[0]) > 128 or not isinstance(upload[1], bytes) or len(upload[1]) > 512 * 1024):
        raise TransportError('invalid_configuration')
    if body is not None and (not isinstance(body, bytes) or len(body) > 700 * 1024 or form is not None or destination is not None):
        raise TransportError('invalid_configuration')
    if (headers is not None and (not isinstance(headers, dict) or set(headers) - {'Cookie', 'Referer', 'Content-Type', 'X-Transmission-Session-Id'}
            or any(not isinstance(v, str) or len(v) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in v) for v in headers.values()))
            or upload_field not in ('name', 'torrents') or set(accepted_statuses) - {200, 204, 400, 401, 403, 409, 415}
            or destination is not None and (headers or accepted_statuses != (200,) or accept_empty)):
        raise TransportError('invalid_configuration')
    if destination is not None:
        if policy.allow_magnet_redirect: raise TransportError('invalid_configuration')
        try:
            destination.lstat()
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError('Acquisition destination already exists')
    if type(max_bytes) is not int or max_bytes <= 0:
        raise TransportError('invalid_limits')
    with tempfile.TemporaryDirectory(prefix='.acquisition-', dir=destination.parent if destination else None) as private:
        staged = Path(private) / 'download.part' if destination else None
        payload = json.dumps({'url': url, 'policy': asdict(policy), 'destination': str(staged) if staged else None, 'max_bytes': max_bytes, 'form': form, 'body': base64.b64encode(body).decode('ascii') if body is not None else None, 'headers': headers, 'accepted_statuses': accepted_statuses, 'accept_empty': accept_empty, 'upload_field': upload_field,
            'upload': [upload[0], base64.b64encode(upload[1]).decode('ascii')] if upload else None}).encode()
        if len(payload) > 1024 * 1024:
            raise TransportError('invalid_configuration')
        # communicate(input=...) can leave a partially written pipe across its
        # short polling timeouts. An owned 0600 temporary file gives the child
        # complete input and EOF, independent of when it starts reading.
        with tempfile.TemporaryFile(dir=private) as request_input:
            request_input.write(payload)
            request_input.seek(0)
            child = subprocess.Popen([sys.executable, '-c',
                                      "import runpy,sys; runpy.run_path(sys.argv[1], run_name='__main__')",
                                      str(Path(__file__).resolve()), '--worker'],
                                     stdin=request_input, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            started = time.monotonic()
            try:
                while True:
                    checkpoint()
                    remaining = policy.deadline - (time.monotonic() - started)
                    if remaining <= 0:
                        raise TransportError('transfer_timeout')
                    try:
                        output, _ = child.communicate(timeout=min(0.2, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        pass
                if child.returncode == 124:
                    raise TransportError('transfer_timeout')
                if child.returncode != 0:
                    raise TransportError('transfer_failed')
                try:
                    result = json.loads(output)
                except (ValueError, UnicodeError):
                    raise TransportError('transfer_failed') from None
                if result.get('error'):
                    raise TransportError(result['error'], retry_after=result.get('retry_after'))
                checkpoint()
                if destination:
                    # link, unlike replace/rename, cannot overwrite an existing file.
                    os.link(staged, destination)
                    return DownloadedFile(destination, result['bytes'], result['sha256'], result['content_type'])
                return FetchedDocument(base64.b64decode(result['body'], validate=True), result['url'], result['content_type'], result.get('status', 200), result.get('headers', {}))
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate()


def _worker_main():
    import json
    import sys
    import threading
    from dataclasses import asdict
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise TransportError('invalid_configuration')
        message = json.loads(raw)
        policy = HTTPPolicy(**message['policy'])
        parent = os.getppid()
        started = time.monotonic()
        # This watchdog may terminate ONLY this dedicated child. Its parent owns
        # temporary-directory cleanup; startup reconciliation handles parent loss.
        def watchdog():
            while True:
                if os.getppid() != parent or time.monotonic() - started >= policy.deadline:
                    os._exit(124)
                time.sleep(0.1)
        threading.Thread(target=watchdog, daemon=True).start()
        if message['destination']:
            result = download_file(message['url'], policy, Path(message['destination']), max_bytes=message['max_bytes'])
            output = {'bytes': result.bytes, 'sha256': result.sha256, 'content_type': result.content_type}
        else:
            upload = message.get('upload')
            result = fetch_document(message['url'], policy, max_bytes=message['max_bytes'],
                form=message.get('form'), body=base64.b64decode(message['body'], validate=True) if message.get('body') is not None else None, headers=message.get('headers'), accepted_statuses=message.get('accepted_statuses', [200]), accept_empty=message.get('accept_empty', False), upload_field=message.get('upload_field', 'name'), upload=(upload[0], base64.b64decode(upload[1], validate=True)) if upload else None)
            output = {'body': base64.b64encode(result.body).decode('ascii'), 'url': result.url, 'content_type': result.content_type, 'status': result.status, 'headers': result.headers}
    except TransportError as exc:
        output = {'error': exc.code, 'retry_after': exc.retry_after}
    except Exception:
        output = {'error': 'transfer_failed'}
    sys.stdout.write(json.dumps(output))


if __name__ == '__main__':
    _worker_main()
