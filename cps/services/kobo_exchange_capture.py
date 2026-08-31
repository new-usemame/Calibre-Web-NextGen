# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Private, passive capture of Kobo Reading Services exchanges.

The observer is deliberately harder to enable than an ordinary boolean flag:
``CWNG_KOBO_READING_SERVICES_CAPTURE`` must exactly equal
``I_UNDERSTAND_THIS_CAPTURES_PRIVATE_READING_DATA``.  Records live beside the
application database in a hidden, mode-0700 directory.  They are not included
in the annotation backup format or any repository artifact.

Each gzip record contains explicit authentication provenance, the device
request, the exact request body actually sent upstream, Kobo's response, and
the final response returned to the device. Credential-bearing headers are
redacted. Annotation text remains only in the private record; ordinary logs
contain structural counts and capture IDs only.

Unauthenticated captures are opt-in, carry no user identity, and use a tighter
independent store so untrusted traffic cannot evict authenticated diagnostics
or unresolved recovery records. All capture persistence runs off the gevent
hub behind a hard request deadline.

This module must never participate in request success.  All public mutators are
best-effort, and :meth:`CaptureSession.finish` swallows storage failures.
"""

from __future__ import annotations

import base64
import fcntl
import gzip
import hashlib
import json
import logging
import os
import secrets
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from gevent import Timeout, get_hub

from .. import constants


log = logging.getLogger(__name__)

ENABLE_ENV = "CWNG_KOBO_READING_SERVICES_CAPTURE"
ENABLE_ACKNOWLEDGEMENT = "I_UNDERSTAND_THIS_CAPTURES_PRIVATE_READING_DATA"

# A single unexpectedly huge exchange is skipped whole; a partial body is less
# useful than no body and could mislead the hardware analysis.  The on-disk set
# is independently bounded after gzip compression.
MAX_BODY_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILES = 256
MAX_AGE_SECONDS = 7 * 24 * 60 * 60

# An unauthenticated refusal has no replay identity and is reachable by anyone
# who can reach the port.  Keep it out of both the recovery spool and the
# authenticated diagnostic budget, with tighter independent bounds.
UNAUTHENTICATED_MAX_BODY_BYTES = 1 * 1024 * 1024
UNAUTHENTICATED_MAX_TOTAL_BYTES = 8 * 1024 * 1024
UNAUTHENTICATED_MAX_FILES = 32
UNAUTHENTICATED_MAX_AGE_SECONDS = 24 * 60 * 60
REQUEST_IO_TIMEOUT_SECONDS = 0.1

_PROCESS_LOCKS = {
    "authenticated": threading.Lock(),
    "unauthenticated": threading.Lock(),
}
_REQUEST_IO_GATES = {
    "authenticated": threading.Lock(),
    "unauthenticated": threading.Lock(),
}


class _CaptureDeadlineExceeded(Exception):
    pass


class _CaptureUnavailable(Exception):
    pass


def _capture_root() -> Path:
    return (
        Path(constants.CONFIG_DIR)
        / ".cwng-private-observability"
        / "kobo-reading-services"
    )


def _unauthenticated_capture_root() -> Path:
    return (
        Path(constants.CONFIG_DIR)
        / ".cwng-private-observability"
        / "kobo-reading-services-unauthenticated"
    )


def enabled(environ=None) -> bool:
    """Return true only for the exact, case-sensitive acknowledgement."""
    environ = os.environ if environ is None else environ
    return environ.get(ENABLE_ENV) == ENABLE_ACKNOWLEDGEMENT


def _sensitive_header(name: str) -> bool:
    normalized = str(name).strip().casefold().replace("_", "-")
    if normalized in {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-kobo-userkey",
    }:
        return True
    return (
        "authorization" in normalized
        or "cookie" in normalized
        or "secret" in normalized
        or "token" in normalized
        or normalized.endswith("-api-key")
        or normalized.endswith("-user-key")
    )


def _redacted_headers(headers) -> list[list[str]]:
    result = []
    for name, value in headers or ():
        result.append([
            str(name),
            "***REDACTED***" if _sensitive_header(name) else str(value),
        ])
    return result


def _body_record(body: bytes) -> dict:
    body = bytes(body or b"")
    try:
        data = body.decode("utf-8", errors="strict")
        encoding = "utf-8"
    except UnicodeDecodeError:
        data = base64.b64encode(body).decode("ascii")
        encoding = "base64"
    return {
        "encoding": encoding,
        "length": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "data": data,
    }


def _leg(*, method=None, path=None, query_string=b"", headers=(), body=b"", status=None):
    value = {
        "headers": _redacted_headers(headers),
        "body": _body_record(body),
    }
    if method is not None:
        value["method"] = str(method)
    if path is not None:
        value["path"] = str(path)
        value["query_string"] = _body_record(bytes(query_string or b""))
    if status is not None:
        value["status"] = int(status)
    return value


def _body_is_within_bound(body, *, max_bytes=MAX_BODY_BYTES) -> bool:
    try:
        return len(body or b"") <= max_bytes
    except TypeError:
        return False


def _run_off_hub_bounded(scope, function):
    """Run diagnostic storage on a native thread with a bounded greenlet wait."""
    gate = _REQUEST_IO_GATES[scope]
    if not gate.acquire(blocking=False):
        raise _CaptureUnavailable("another capture storage operation is active")
    try:
        timeout = Timeout.start_new(REQUEST_IO_TIMEOUT_SECONDS)
    except BaseException:
        gate.release()
        raise
    try:
        try:
            result = get_hub().threadpool.spawn(
                _capture_worker_result, function, gate,
            )
        except BaseException:
            gate.release()
            raise
        value, error = result.get()
        if error is not None:
            raise error
        return value
    except Timeout as error:
        if error is not timeout:
            raise
        raise _CaptureDeadlineExceeded(
            f"Kobo exchange capture exceeded {REQUEST_IO_TIMEOUT_SECONDS:.3f}s deadline"
        ) from None
    finally:
        timeout.cancel()


def _capture_worker_result(function, gate):
    try:
        try:
            return function(), None
        except Exception as error:
            return None, error
    finally:
        gate.release()


def begin_capture(
    *, exchange, method, path, query_string, headers, body,
    authentication="not_recorded", user_id=None,
):
    """Begin an exchange capture, or return ``None`` when off/out of bounds."""
    if not enabled():
        return None
    if authentication not in {
        "authenticated", "unauthenticated", "not_recorded",
    }:
        log.warning("Kobo exchange capture skipped: invalid authentication provenance")
        return None
    if authentication == "unauthenticated" and user_id is not None:
        log.warning("Kobo exchange capture skipped: unauthenticated record has a user id")
        return None
    max_body_bytes = (
        UNAUTHENTICATED_MAX_BODY_BYTES
        if authentication == "unauthenticated"
        else MAX_BODY_BYTES
    )
    if not _body_is_within_bound(body, max_bytes=max_body_bytes):
        log.warning(
            "Kobo exchange capture skipped: device request exceeds body bound exchange=%s bytes=%s",
            str(exchange)[:64], len(body or b""),
        )
        return None
    try:
        return CaptureSession(
            exchange=exchange,
            method=method,
            path=path,
            query_string=query_string,
            headers=headers,
            body=body,
            authentication=authentication,
            user_id=user_id,
        )
    except Exception:
        log.warning(
            "Kobo exchange capture could not start exchange=%s",
            str(exchange)[:64], exc_info=True,
        )
        return None


class CaptureSession:
    """In-memory exchange envelope committed atomically after the response."""

    def __init__(
        self, *, exchange, method, path, query_string, headers, body,
        authentication, user_id,
    ):
        self.capture_id = secrets.token_hex(16)
        self._invalid = False
        self._finished = False
        self._authentication = authentication
        self._storage_scope = (
            "unauthenticated"
            if authentication == "unauthenticated"
            else "authenticated"
        )
        self._max_body_bytes = (
            UNAUTHENTICATED_MAX_BODY_BYTES
            if authentication == "unauthenticated"
            else MAX_BODY_BYTES
        )
        self._record = {
            "schema_version": 2,
            "capture_id": self.capture_id,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "exchange": str(exchange)[:64],
            "request_provenance": {
                "authentication": authentication,
                "user_id": user_id,
            },
            "device_request": _leg(
                method=method,
                path=path,
                query_string=query_string,
                headers=headers,
                body=body,
            ),
            "upstream_request": None,
            "upstream_response": None,
            "upstream_error": None,
            "device_response": None,
            "decisions": [],
        }

    def _mutate(self, callback) -> bool:
        if self._finished:
            return False
        try:
            callback()
            return True
        except Exception:
            self._invalid = True
            log.warning(
                "Kobo exchange capture observation failed capture_id=%s",
                self.capture_id, exc_info=True,
            )
            return False

    def add_decision(self, *, stage, index, content_id, ownership, authority_status, action):
        return self._mutate(lambda: self._record["decisions"].append({
            "stage": str(stage)[:32],
            "index": int(index),
            "content_id": str(content_id),
            "ownership": str(ownership)[:16],
            "authority_status": (
                None if authority_status is None else str(authority_status)[:32]
            ),
            "action": str(action)[:32],
        }))

    def record_upstream_request(
        self, *, method, path, query_string, headers, body,
    ) -> bool:
        if not _body_is_within_bound(body, max_bytes=self._max_body_bytes):
            self._invalid = True
            return False
        return self._mutate(lambda: self._record.__setitem__(
            "upstream_request",
            _leg(
                method=method,
                path=path,
                query_string=query_string,
                headers=headers,
                body=body,
            ),
        ))

    def record_upstream_response(self, *, status, headers, body) -> bool:
        if not _body_is_within_bound(body, max_bytes=self._max_body_bytes):
            self._invalid = True
            return False
        return self._mutate(lambda: self._record.__setitem__(
            "upstream_response",
            _leg(status=status, headers=headers, body=body),
        ))

    def record_upstream_error(self, error_kind) -> bool:
        return self._mutate(lambda: self._record.__setitem__(
            "upstream_error", str(error_kind)[:64],
        ))

    def finish(self, *, status, headers, body) -> bool:
        """Persist the complete envelope; never raise into the observed route."""
        if self._finished:
            return False
        self._finished = True
        if not _body_is_within_bound(body, max_bytes=self._max_body_bytes):
            self._invalid = True
        try:
            self._record["device_response"] = _leg(
                status=status, headers=headers, body=body,
            )
            if self._invalid:
                log.warning(
                    "Kobo exchange capture skipped incomplete envelope capture_id=%s",
                    self.capture_id,
                )
                return False
            _run_off_hub_bounded(self._storage_scope, self._persist)
            log.info(
                "Kobo exchange captured capture_id=%s exchange=%s",
                self.capture_id, self._record["exchange"],
            )
            return True
        except (_CaptureDeadlineExceeded, _CaptureUnavailable):
            log.warning(
                "Kobo exchange capture persistence unavailable capture_id=%s scope=%s",
                self.capture_id, self._authentication,
            )
            return False
        except Exception:
            log.warning(
                "Kobo exchange capture persistence failed capture_id=%s",
                self.capture_id, exc_info=True,
            )
            return False

    def _persist(self):
        serialized = json.dumps(
            self._record,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if self._authentication == "unauthenticated":
            root = _unauthenticated_capture_root()
            max_files = UNAUTHENTICATED_MAX_FILES
            max_total_bytes = UNAUTHENTICATED_MAX_TOTAL_BYTES
            max_age_seconds = UNAUTHENTICATED_MAX_AGE_SECONDS
        else:
            root = _capture_root()
            max_files = MAX_FILES
            max_total_bytes = MAX_TOTAL_BYTES
            max_age_seconds = MAX_AGE_SECONDS
        compressed = gzip.compress(serialized, compresslevel=6, mtime=0)
        if len(compressed) > max_total_bytes:
            raise ValueError("one compressed capture exceeds total retention bound")
        with _PROCESS_LOCKS[self._storage_scope]:
            # mkdir(parents=True) does NOT apply `mode` to the intermediates it
            # creates, so the private-observability parent would land at
            # 0777 & ~umask while only the leaf got 0700. Create and tighten it
            # explicitly.
            root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(root.parent, 0o700)
            root.mkdir(exist_ok=True, mode=0o700)
            os.chmod(root, 0o700)
            lock_path = root / ".capture.lock"
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                os.fchmod(lock_fd, 0o600)
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                _prune_locked(
                    root,
                    incoming_bytes=len(compressed),
                    max_files=max_files,
                    max_total_bytes=max_total_bytes,
                    max_age_seconds=max_age_seconds,
                )
                final_path = root / (
                    f"exchange-{time.time_ns():020d}-{self.capture_id}.json.gz"
                )
                temp_fd, temp_name = tempfile.mkstemp(
                    prefix=".capture-", suffix=".tmp", dir=root,
                )
                try:
                    os.fchmod(temp_fd, 0o600)
                    with os.fdopen(temp_fd, "wb") as stream:
                        temp_fd = None
                        stream.write(compressed)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temp_name, final_path)
                    directory_fd = os.open(root, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                    _prune_locked(
                        root,
                        incoming_bytes=0,
                        max_files=max_files,
                        max_total_bytes=max_total_bytes,
                        max_age_seconds=max_age_seconds,
                    )
                finally:
                    if temp_fd is not None:
                        os.close(temp_fd)
                    try:
                        os.unlink(temp_name)
                    except FileNotFoundError:
                        pass
            finally:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)


def _capture_paths(root: Path) -> list[Path]:
    paths = list(root.glob("exchange-*.json.gz"))
    paths.sort(key=lambda path: (path.stat().st_mtime_ns, path.name))
    return paths


def _file_count_requires_prune(
    count: int, *, incoming: bool, max_files=None,
) -> bool:
    """Use make-room semantics before a write and hard-limit semantics after."""
    if max_files is None:
        max_files = MAX_FILES
    return count >= max_files if incoming else count > max_files


def _prune_locked(
    root: Path, *, incoming_bytes: int, max_files=None,
    max_total_bytes=None, max_age_seconds=None,
):
    if max_files is None:
        max_files = MAX_FILES
    if max_total_bytes is None:
        max_total_bytes = MAX_TOTAL_BYTES
    if max_age_seconds is None:
        max_age_seconds = MAX_AGE_SECONDS
    now = time.time()
    paths = _capture_paths(root)
    for path in list(paths):
        try:
            if now - path.stat().st_mtime > max_age_seconds:
                path.unlink()
                paths.remove(path)
        except FileNotFoundError:
            paths.remove(path)

    total = sum(path.stat().st_size for path in paths if path.exists())
    while paths and (
        _file_count_requires_prune(
            len(paths), incoming=bool(incoming_bytes), max_files=max_files,
        )
        or total + incoming_bytes > max_total_bytes
    ):
        oldest = paths.pop(0)
        try:
            size = oldest.stat().st_size
            oldest.unlink()
            total -= size
        except FileNotFoundError:
            pass

    if incoming_bytes and (
        max_files < 1 or total + incoming_bytes > max_total_bytes
    ):
        raise ValueError("capture retention bounds leave no room for exchange")
