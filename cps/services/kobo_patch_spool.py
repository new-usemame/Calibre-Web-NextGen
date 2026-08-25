# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, durable recovery spool for raw Kobo annotation PATCH bodies.

Unlike the opt-in exchange observer, this is an always-on data-integrity
primitive. A successful stage returns only after the exact body and every new
directory entry are fsynced, before JSON parsing or local dispatch begins.
Storage runs on gevent's native threadpool behind a hard request deadline: a
busy or wedged spool returns no ticket and never blocks the worker hub without
bound.

Unresolved records (``staged`` / ``dispatch_exception``) are never evicted to
admit a new body. Admission and outcome replacement use a small durable
transaction journal, so a failed or interrupted write restores the prior
record set. Successful records can be evicted within the advertised count and
compressed-byte bounds. A deadline-driven maintenance thread enforces age
retention even when no later PATCH arrives.

The spool stores no request headers or credentials. It is a local recovery
artifact, excluded from annotation backups and support bundles.
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

MAX_BODY_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILES = 512
MAX_AGE_SECONDS = 14 * 24 * 60 * 60
REQUEST_IO_TIMEOUT_SECONDS = 0.1

_PROCESS_LOCK = threading.Lock()
_REQUEST_IO_GATE = threading.Lock()
_VALID_OUTCOMES = {"staged", "dispatch_completed", "dispatch_exception"}
_UNRESOLVED_OUTCOMES = {"staged", "dispatch_exception"}
_RETENTION_TIMERS_LOCK = threading.Lock()
_RETENTION_TIMERS = {}
_RETENTION_STARTED = False


class _SpoolNoRoom(Exception):
    pass


class _SpoolDeadlineExceeded(Exception):
    pass


class _SpoolUnavailable(Exception):
    pass


class _SpoolCancelled(Exception):
    pass


def _spool_root() -> Path:
    return (
        Path(constants.CONFIG_DIR)
        / ".cwng-private-observability"
        / "kobo-patch-spool"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(bytes(value)).hexdigest()


def _body_within_bound(raw_body) -> bool:
    try:
        return len(raw_body) <= MAX_BODY_BYTES
    except TypeError:
        return False


def is_replay_candidate(status) -> bool:
    return status in _UNRESOLVED_OUTCOMES


def _run_off_hub_bounded(function, *args):
    """Run blocking spool I/O off-hub and bound the caller's wait."""
    if not _REQUEST_IO_GATE.acquire(blocking=False):
        raise _SpoolUnavailable("another spool storage operation is still active")
    cancelled = threading.Event()
    try:
        timeout = Timeout.start_new(REQUEST_IO_TIMEOUT_SECONDS)
    except BaseException:
        _REQUEST_IO_GATE.release()
        raise
    try:
        try:
            result = get_hub().threadpool.spawn(
                _capture_worker_result, function, args, cancelled,
            )
        except BaseException:
            _REQUEST_IO_GATE.release()
            raise
        value, error = result.get()
        if error is not None:
            raise error
        return value
    except Timeout as error:
        if error is not timeout:
            raise
        cancelled.set()
        raise _SpoolDeadlineExceeded(
            f"Kobo PATCH spool exceeded {REQUEST_IO_TIMEOUT_SECONDS:.3f}s deadline"
        ) from None
    finally:
        timeout.cancel()


def _capture_worker_result(function, args, cancelled):
    """Return worker errors as values so gevent does not log expected failures."""
    try:
        try:
            return function(*args, cancelled), None
        except Exception as error:
            return None, error
    finally:
        _REQUEST_IO_GATE.release()


def stage_patch(*, raw_body, entitlement_id, user_id, origin_device_id):
    """Durably stage an exact PATCH body, or fail open with no ticket."""
    if not _body_within_bound(raw_body):
        log.error(
            "Kobo PATCH recovery spool skipped body outside bound bytes=%s",
            len(raw_body) if isinstance(raw_body, (bytes, bytearray)) else None,
        )
        return None
    try:
        raw_body = bytes(raw_body)
        spool_id = secrets.token_hex(16)
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "schema_version": 1,
            "spool_id": spool_id,
            "received_at": now,
            "entitlement_id": str(entitlement_id),
            "user_id": user_id,
            "origin_device_id": origin_device_id,
            "body_encoding": "base64",
            "body_length": len(raw_body),
            "body_sha256": sha256_bytes(raw_body),
            "body_base64": base64.b64encode(raw_body).decode("ascii"),
            "dispatch_status": "staged",
            "dispatch_updated_at": now,
        }
        compressed = _compress(record)
        path = _run_off_hub_bounded(_write_new_record, spool_id, compressed)
        log.info(
            "Kobo PATCH recovery body staged spool_id=%s user_id=%s bytes=%s",
            spool_id, user_id, len(raw_body),
        )
        return PatchSpoolTicket(spool_id=spool_id, path=path)
    except _SpoolNoRoom:
        log.warning(
            "Kobo PATCH recovery spool full of established unresolved records; "
            "new body not staged user_id=%s bytes=%s",
            user_id, len(raw_body),
        )
        return None
    except _SpoolDeadlineExceeded:
        log.error(
            "Kobo PATCH recovery spool timed out; new body not staged "
            "user_id=%s bytes=%s deadline_ms=%s",
            user_id, len(raw_body), int(REQUEST_IO_TIMEOUT_SECONDS * 1000),
        )
        return None
    except _SpoolUnavailable:
        log.warning(
            "Kobo PATCH recovery spool busy or unavailable; new body not "
            "staged user_id=%s bytes=%s",
            user_id, len(raw_body),
        )
        return None
    except Exception:
        log.error(
            "Kobo PATCH recovery spool failed user_id=%s bytes=%s",
            user_id,
            len(raw_body) if isinstance(raw_body, (bytes, bytearray)) else None,
            exc_info=True,
        )
        return None


class PatchSpoolTicket:
    def __init__(self, *, spool_id, path):
        self.spool_id = spool_id
        self.path = Path(path)

    def mark_dispatch_outcome(self, status) -> bool:
        if status not in _VALID_OUTCOMES - {"staged"}:
            raise ValueError("invalid Kobo PATCH dispatch outcome")
        try:
            new_path = _run_off_hub_bounded(
                _mark_dispatch_outcome_blocking, self.path, status,
            )
            self.path = Path(new_path)
            return True
        except (_SpoolNoRoom, _SpoolDeadlineExceeded, _SpoolUnavailable):
            log.error(
                "Kobo PATCH recovery outcome update unavailable; preserving "
                "staged record spool_id=%s status=%s",
                self.spool_id, status,
            )
            return False
        except Exception:
            log.error(
                "Kobo PATCH recovery outcome update failed spool_id=%s status=%s",
                self.spool_id, status, exc_info=True,
            )
            return False


class _RootLock:
    def __init__(self, root):
        self.root = Path(root)
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.root / ".spool.lock", os.O_CREAT | os.O_RDWR, 0o600)
        os.fchmod(self.fd, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


def _locked_root(root):
    return _RootLock(root)


def _ensure_not_cancelled(cancelled):
    if cancelled is not None and cancelled.is_set():
        raise _SpoolCancelled("spool request deadline elapsed")


def _compress(record) -> bytes:
    serialized = json.dumps(
        record, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return gzip.compress(serialized, compresslevel=6, mtime=0)


def _fsync_directory(path):
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _ensure_private_root(root):
    root = Path(root)
    private_parent_created = not root.parent.exists()
    root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root.parent, 0o700)
    if private_parent_created:
        _fsync_directory(root.parent.parent)

    root_created = not root.exists()
    root.mkdir(exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    if root_created:
        _fsync_directory(root.parent)


def _record_entries(root):
    entries = []
    for path in Path(root).glob("patch-*.json.gz"):
        try:
            stat_result = path.stat()
        except FileNotFoundError:
            continue
        entries.append((stat_result.st_mtime_ns, path.name, path, stat_result))
    entries.sort(key=lambda item: item[:2])
    return entries


def _record_paths(root) -> list[Path]:
    return [path for _mtime, _name, path, _stat in _record_entries(root)]


def _status_from_path(path):
    stem = Path(path).name.removesuffix(".json.gz")
    for status in _VALID_OUTCOMES:
        if stem.endswith(f"-{status}"):
            return status
    return None


def _path_with_status(path, status):
    path = Path(path)
    stem = path.name.removesuffix(".json.gz")
    for known_status in _VALID_OUTCOMES:
        suffix = f"-{known_status}"
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    return path.with_name(f"{stem}-{status}.json.gz")


def _record_inventory(root):
    inventory = []
    for _mtime, _name, path, stat_result in _record_entries(root):
        try:
            status = _status_from_path(path)
            if status is None:
                status = _load_disk_record(path).get("dispatch_status")
            inventory.append({
                "path": path,
                "size": stat_result.st_size,
                "mtime": stat_result.st_mtime,
                "status": status,
            })
        except FileNotFoundError:
            continue
        except Exception:
            log.error(
                "Unreadable Kobo PATCH recovery record protected from pruning path=%s",
                path.name, exc_info=True,
            )
            inventory.append({
                "path": path,
                "size": stat_result.st_size,
                "mtime": stat_result.st_mtime,
                "status": None,
            })
    return inventory


def _select_victims(inventory, *, incoming_bytes, replacing_path=None):
    replacing_path = Path(replacing_path) if replacing_path is not None else None
    active = [item for item in inventory if item["path"] != replacing_path]
    victims = []
    now = time.time()
    for item in active:
        if now - item["mtime"] > MAX_AGE_SECONDS:
            victims.append(item)

    survivors = [item for item in active if item not in victims]
    count = len(survivors) + 1
    total = sum(item["size"] for item in survivors) + incoming_bytes
    if incoming_bytes > MAX_TOTAL_BYTES or MAX_FILES < 1:
        raise _SpoolNoRoom("incoming record cannot fit spool bounds")

    for item in survivors:
        if count <= MAX_FILES and total <= MAX_TOTAL_BYTES:
            break
        if item["status"] != "dispatch_completed":
            continue
        victims.append(item)
        count -= 1
        total -= item["size"]

    if count > MAX_FILES or total > MAX_TOTAL_BYTES:
        raise _SpoolNoRoom("established unresolved records consume spool bounds")
    return [item["path"] for item in victims]


def _transaction_journal(root, token):
    return Path(root) / f".txn-{token}.json"


def _write_journal(path, payload):
    serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    _replace_record_locked(path, serialized)


def _recover_transactions_locked(root):
    root = Path(root)
    for journal_path in root.glob(".txn-*.json"):
        try:
            payload = json.loads(journal_path.read_text(encoding="utf-8"))
            final_path = root / payload["final"]
            prepared_path = root / payload["prepared"]
            mappings = [
                (root / item["original"], root / item["retired"])
                for item in payload["mappings"]
            ]
            if payload.get("state") == "committed" and final_path.exists():
                for _original, retired in mappings:
                    retired.unlink(missing_ok=True)
            else:
                final_path.unlink(missing_ok=True)
                for original, retired in reversed(mappings):
                    if retired.exists():
                        if original.exists():
                            retired.unlink()
                        else:
                            os.replace(retired, original)
            prepared_path.unlink(missing_ok=True)
            journal_path.unlink()
            _fsync_directory(root)
        except Exception:
            log.error(
                "Kobo PATCH recovery transaction could not be recovered journal=%s",
                journal_path.name, exc_info=True,
            )

    removed = False
    for path in root.glob(".incoming-*.json.gz"):
        try:
            path.unlink()
            removed = True
        except FileNotFoundError:
            pass
    if removed:
        _fsync_directory(root)


def _install_record_locked(
    final_path, compressed, *, victims, replacing_path=None, cancelled=None,
):
    """Install one record with crash-recoverable eviction/replacement."""
    final_path = Path(final_path)
    root = final_path.parent
    token = secrets.token_hex(12)
    prepared_path = root / f".incoming-{token}.json.gz"
    journal_path = _transaction_journal(root, token)
    sources = []
    if replacing_path is not None:
        sources.append(Path(replacing_path))
    sources.extend(Path(path) for path in victims if Path(path) not in sources)
    mappings = [
        (source, root / f".retired-{token}-{index}.json.gz")
        for index, source in enumerate(sources)
    ]
    journal = {
        "schema_version": 1,
        "state": "prepared",
        "final": final_path.name,
        "prepared": prepared_path.name,
        "mappings": [
            {"original": original.name, "retired": retired.name}
            for original, retired in mappings
        ],
    }
    final_installed = False
    committed = False
    try:
        _replace_record_locked(prepared_path, compressed, cancelled=cancelled)
        _ensure_not_cancelled(cancelled)
        _write_journal(journal_path, journal)
        for original, retired in mappings:
            os.replace(original, retired)
        _fsync_directory(root)
        _ensure_not_cancelled(cancelled)

        os.replace(prepared_path, final_path)
        final_installed = True
        _fsync_directory(root)
        _ensure_not_cancelled(cancelled)

        journal["state"] = "committed"
        _write_journal(journal_path, journal)
        committed = True
    except Exception:
        if not committed:
            if final_installed:
                final_path.unlink(missing_ok=True)
            for original, retired in reversed(mappings):
                if retired.exists():
                    if original.exists():
                        retired.unlink()
                    else:
                        os.replace(retired, original)
            prepared_path.unlink(missing_ok=True)
            journal_path.unlink(missing_ok=True)
            try:
                _fsync_directory(root)
            except OSError:
                pass
        raise
    finally:
        prepared_path.unlink(missing_ok=True)

    try:
        for _original, retired in mappings:
            retired.unlink(missing_ok=True)
        journal_path.unlink(missing_ok=True)
        _fsync_directory(root)
    except OSError:
        log.error(
            "Kobo PATCH recovery transaction cleanup deferred token=%s", token,
            exc_info=True,
        )


def _write_new_record(spool_id, compressed, cancelled=None):
    root = _spool_root()
    with _PROCESS_LOCK:
        _ensure_not_cancelled(cancelled)
        _ensure_private_root(root)
        with _locked_root(root):
            _ensure_not_cancelled(cancelled)
            _recover_transactions_locked(root)
            inventory = _record_inventory(root)
            victims = _select_victims(
                inventory, incoming_bytes=len(compressed),
            )
            path = root / (
                f"patch-{time.time_ns():020d}-{spool_id}-staged.json.gz"
            )
            _schedule_retention(root, time.time() + MAX_AGE_SECONDS)
            _install_record_locked(
                path, compressed, victims=victims, cancelled=cancelled,
            )
    return path


def _mark_dispatch_outcome_blocking(path, status, cancelled=None):
    path = Path(path)
    root = path.parent
    with _PROCESS_LOCK:
        _ensure_not_cancelled(cancelled)
        _ensure_private_root(root)
        with _locked_root(root):
            _ensure_not_cancelled(cancelled)
            _recover_transactions_locked(root)
            record = _load_disk_record(path)
            record["dispatch_status"] = status
            record["dispatch_updated_at"] = datetime.now(timezone.utc).isoformat()
            compressed = _compress(record)
            inventory = _record_inventory(root)
            victims = _select_victims(
                inventory, incoming_bytes=len(compressed), replacing_path=path,
            )
            new_path = _path_with_status(path, status)
            _install_record_locked(
                new_path,
                compressed,
                victims=victims,
                replacing_path=path,
                cancelled=cancelled,
            )
    return new_path


def _replace_record_locked(path, compressed, cancelled=None):
    path = Path(path)
    temp_fd, temp_name = tempfile.mkstemp(
        prefix=".patch-", suffix=".tmp", dir=path.parent,
    )
    try:
        os.fchmod(temp_fd, 0o600)
        with os.fdopen(temp_fd, "wb") as stream:
            temp_fd = None
            stream.write(compressed)
            stream.flush()
            os.fsync(stream.fileno())
        _ensure_not_cancelled(cancelled)
        os.replace(temp_name, path)
        _fsync_directory(path.parent)
        _ensure_not_cancelled(cancelled)
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _load_disk_record(path):
    return json.loads(gzip.decompress(Path(path).read_bytes()))


def load_spooled_patch(path):
    """Load routing metadata plus the exact raw body for controlled replay."""
    record = _load_disk_record(path)
    encoded = record.pop("body_base64")
    body = base64.b64decode(encoded, validate=True)
    if len(body) != record["body_length"] or sha256_bytes(body) != record["body_sha256"]:
        raise ValueError("Kobo PATCH spool body integrity check failed")
    record["body"] = body
    return record


def _expire_root_blocking(root):
    root = Path(root)
    if not root.exists():
        return None
    with _PROCESS_LOCK:
        with _locked_root(root):
            _recover_transactions_locked(root)
            inventory = _record_inventory(root)
            now = time.time()
            expired = [
                item for item in inventory
                if now - item["mtime"] > MAX_AGE_SECONDS
            ]
            for item in expired:
                item["path"].unlink(missing_ok=True)
            if expired:
                _fsync_directory(root)
            survivors = [item for item in inventory if item not in expired]
            if not survivors:
                return None
            return min(item["mtime"] + MAX_AGE_SECONDS for item in survivors)


def _retention_timer_fired(root, deadline):
    key = str(Path(root))
    with _RETENTION_TIMERS_LOCK:
        current = _RETENTION_TIMERS.get(key)
        if current is not None and current[0] == deadline:
            _RETENTION_TIMERS.pop(key, None)
    try:
        next_deadline = _expire_root_blocking(root)
    except Exception:
        log.error("Kobo PATCH recovery age maintenance failed", exc_info=True)
        next_deadline = time.time() + 1.0
    if next_deadline is not None:
        _schedule_retention(root, next_deadline)


def _schedule_retention(root, deadline):
    root = Path(root)
    key = str(root)
    with _RETENTION_TIMERS_LOCK:
        current = _RETENTION_TIMERS.get(key)
        if current is not None and current[0] <= deadline:
            return
        if current is not None:
            current[1].cancel()
        timer = threading.Timer(
            max(0.001, deadline - time.time()),
            _retention_timer_fired,
            args=(root, deadline),
        )
        timer.daemon = True
        _RETENTION_TIMERS[key] = (deadline, timer)
        timer.start()


def _bootstrap_retention():
    root = _spool_root()
    try:
        next_deadline = _expire_root_blocking(root)
    except Exception:
        log.error("Kobo PATCH recovery startup maintenance failed", exc_info=True)
        return
    if next_deadline is not None:
        _schedule_retention(root, next_deadline)


def start_retention_maintenance():
    """Start startup expiry once per process without delaying app startup."""
    global _RETENTION_STARTED
    with _RETENTION_TIMERS_LOCK:
        if _RETENTION_STARTED:
            return True
        thread = threading.Thread(
            target=_bootstrap_retention,
            name="kobo-patch-spool-retention-bootstrap",
            daemon=True,
        )
        try:
            thread.start()
        except Exception:
            log.error(
                "Kobo PATCH recovery maintenance could not start", exc_info=True,
            )
            return False
        _RETENTION_STARTED = True
        return True


def iter_replay_candidates():
    root = _spool_root()
    if not root.exists():
        return
    try:
        _expire_root_blocking(root)
    except Exception:
        log.error("Kobo PATCH recovery age maintenance failed", exc_info=True)
    for path in _record_paths(root):
        try:
            if is_replay_candidate(_load_disk_record(path).get("dispatch_status")):
                yield path
        except Exception:
            log.error("Unreadable Kobo PATCH recovery record path=%s", path.name, exc_info=True)
