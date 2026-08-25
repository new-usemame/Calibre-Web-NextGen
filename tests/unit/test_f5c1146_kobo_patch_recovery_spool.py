# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""F-5c1146: stage Kobo PATCH bytes before parsing or dispatch."""

from __future__ import annotations

import fcntl
import importlib
import inspect
import json
import multiprocessing
import os
import stat
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

import cps.readingservices as rs


BOOK_UUID = "9e5251ad-d530-4e58-9121-8b8336099fdd"
REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_PATCH = (
    b'{"updatedAnnotations":[{"id":"annotation-1","type":"highlight",'
    b'"highlightedText":"private words"}],"deletedAnnotationIds":[]}'
)


def _module():
    return importlib.import_module("cps.services.kobo_patch_spool")


def _root(monkeypatch, tmp_path):
    spool = _module()
    root = tmp_path / "private-patch-spool"
    monkeypatch.setattr(spool, "_spool_root", lambda: root)
    return spool, root


def _records(spool, root):
    paths = sorted(root.glob("patch-*.json.gz"))
    return [(path, spool.load_spooled_patch(path)) for path in paths]


def _hold_advisory_lock(lock_path, ready, release):
    """Hold the real cross-process spool lock until the parent releases it."""
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        ready.send(True)
        release.recv()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _app(monkeypatch, *, dispatch):
    app = Flask(__name__)

    # GET is registered too: the body-read guard has to behave DIFFERENTLY for
    # GET than for PATCH, and a PATCH-only app makes that assertion vacuous -
    # the GET would 405 and trivially satisfy "not 503".
    @app.route("/annotations/<content_id>", methods=["GET", "PATCH"])
    def annotations(content_id):
        return rs.handle_annotations.__wrapped__(content_id)

    book = SimpleNamespace(id=347, title="Flatland", identifiers=[])
    user = SimpleNamespace(id=7, name="test-user", is_authenticated=True)
    monkeypatch.setattr(rs, "current_user", user)
    monkeypatch.setattr(rs, "resolve_entitlement_ownership", lambda _content_id: book)
    monkeypatch.setattr(rs, "log_annotation_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "cps.services.annotation_sync.dispatch_annotation_sync", dispatch,
    )
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kwargs: app.response_class(
            b'{"upstream":"accepted"}', status=207, headers={"X-Upstream": "same"},
        ),
    )
    return app


@pytest.mark.unit
def test_patch_spool_is_durable_before_parse_and_dispatch(monkeypatch, tmp_path):
    spool, root = _root(monkeypatch, tmp_path)
    source = inspect.getsource(rs.handle_annotations.__wrapped__)
    assert source.index("_stage_patch_for_recovery") < source.index("request.get_json")
    assert source.index("_stage_patch_for_recovery") < source.index("dispatch_annotation_sync")

    def _dispatch(*_args, **_kwargs):
        [(_path, record)] = _records(spool, root)
        assert record["body"] == RAW_PATCH
        assert record["dispatch_status"] == "staged"

    app = _app(monkeypatch, dispatch=_dispatch)
    response = app.test_client().patch(
        f"/annotations/{BOOK_UUID}", data=RAW_PATCH, content_type="application/json",
    )

    assert response.status_code == 207
    assert response.get_data() == b'{"upstream":"accepted"}'
    [(path, record)] = _records(spool, root)
    assert record["body"] == RAW_PATCH
    assert record["body_sha256"] == spool.sha256_bytes(RAW_PATCH)
    assert record["dispatch_status"] == "dispatch_completed"
    assert record["user_id"] == 7
    assert record["entitlement_id"] == BOOK_UUID
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.unit
def test_dispatch_exception_spools_the_body_and_still_refuses_to_acknowledge(
    monkeypatch, tmp_path,
):
    """The spool must not soften the #1825 refusal.

    Spooling makes a lost delta recoverable server-side, which is why the
    response code matters less than it did.  It does not make the PATCH stored,
    so CWNG must still answer 503 rather than let the device retire a delta it
    will never re-send.  Asserting 207 here would silently revert F-5c1146.
    """
    spool, root = _root(monkeypatch, tmp_path)

    def _raise(*_args, **_kwargs):
        raise RuntimeError("dispatch exploded")

    app = _app(monkeypatch, dispatch=_raise)
    response = app.test_client().patch(
        f"/annotations/{BOOK_UUID}", data=RAW_PATCH, content_type="application/json",
    )

    assert response.status_code == 503
    # not the proxied upstream body: we are refusing, not relaying an acceptance
    assert b"upstream" not in response.get_data()
    [(path, record)] = _records(spool, root)
    assert record["body"] == RAW_PATCH
    assert record["dispatch_status"] == "dispatch_exception"
    assert list(spool.iter_replay_candidates()) == [path]
    serialized = json.dumps({key: value for key, value in record.items() if key != "body"})
    assert "private words" not in serialized


@pytest.mark.unit
def test_parse_exception_still_leaves_staged_replay_candidate(monkeypatch, tmp_path):
    spool, root = _root(monkeypatch, tmp_path)
    app = _app(monkeypatch, dispatch=lambda *_args, **_kwargs: None)
    original = app.request_class.get_json

    def _raise_parse(self, *args, **kwargs):
        del self, args, kwargs
        raise ValueError("parser failed")

    monkeypatch.setattr(app.request_class, "get_json", _raise_parse)
    response = app.test_client().patch(
        f"/annotations/{BOOK_UUID}", data=RAW_PATCH, content_type="application/json",
    )
    monkeypatch.setattr(app.request_class, "get_json", original)

    # Same contract as the dispatch-exception case: the body is recoverable, but
    # nothing was stored, so the device must not be told the delta landed.
    assert response.status_code == 503
    [(_path, record)] = _records(spool, root)
    assert record["body"] == RAW_PATCH
    assert record["dispatch_status"] == "dispatch_exception"


@pytest.mark.unit
def test_spool_failure_cannot_change_patch_response_or_dispatch(monkeypatch, tmp_path):
    spool, _root_path = _root(monkeypatch, tmp_path)
    dispatched = []
    app = _app(
        monkeypatch,
        dispatch=lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )
    monkeypatch.setattr(
        spool, "stage_patch",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("read-only config")),
    )

    response = app.test_client().patch(
        f"/annotations/{BOOK_UUID}", data=RAW_PATCH, content_type="application/json",
    )

    assert response.status_code == 207
    assert response.get_data() == b'{"upstream":"accepted"}'
    assert len(dispatched) == 1


@pytest.mark.unit
def test_existing_ownership_unknown_503_is_unchanged_and_body_is_spooled(
    monkeypatch, tmp_path,
):
    spool, root = _root(monkeypatch, tmp_path)
    app = Flask(__name__)

    @app.patch("/annotations/<content_id>")
    def annotations(content_id):
        return rs.handle_annotations.__wrapped__(content_id)

    monkeypatch.setattr(rs, "current_user", SimpleNamespace(id=7, is_authenticated=True))
    monkeypatch.setattr(
        rs, "resolve_entitlement_ownership", lambda _content_id: rs.OWNERSHIP_UNKNOWN,
    )
    monkeypatch.setattr(rs, "log_annotation_data", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kwargs: pytest.fail("the existing 503 branch must not proxy"),
    )

    response = app.test_client().patch(
        f"/annotations/{BOOK_UUID}", data=RAW_PATCH, content_type="application/json",
    )

    assert response.status_code == 503
    assert response.get_json() == {"error": "Annotation capture temporarily unavailable"}
    [(_path, record)] = _records(spool, root)
    assert record["body"] == RAW_PATCH
    assert record["dispatch_status"] == "staged"


@pytest.mark.unit
def test_patch_spool_is_bounded_and_never_stores_request_headers(monkeypatch, tmp_path):
    spool, root = _root(monkeypatch, tmp_path)
    monkeypatch.setattr(spool, "MAX_FILES", 2)
    monkeypatch.setattr(spool, "MAX_TOTAL_BYTES", 1024 * 1024)

    for index in range(4):
        ticket = spool.stage_patch(
            raw_body=f'{{"updatedAnnotations":[],"index":{index}}}'.encode(),
            entitlement_id=f"book-{index}", user_id=7, origin_device_id=None,
        )
        assert ticket is not None
        assert ticket.mark_dispatch_outcome("dispatch_completed") is True

    records = _records(spool, root)
    assert len(records) == 2
    assert [record["entitlement_id"] for _path, record in records] == ["book-2", "book-3"]
    for _path, record in records:
        assert "headers" not in record
        assert "authorization" not in json.dumps(
            {key: value for key, value in record.items() if key != "body"}
        ).lower()
    assert sum(path.stat().st_size for path in root.glob("patch-*.json.gz")) \
        <= spool.MAX_TOTAL_BYTES


@pytest.mark.unit
def test_full_spool_never_evicts_an_unresolved_recovery_record(monkeypatch, tmp_path):
    """A staged body is the only surviving copy and is not safe to prune."""
    spool, root = _root(monkeypatch, tmp_path)
    monkeypatch.setattr(spool, "MAX_FILES", 1)
    monkeypatch.setattr(spool, "MAX_TOTAL_BYTES", 1024 * 1024)

    first = spool.stage_patch(
        raw_body=b'{"updatedAnnotations":[{"id":"must-survive"}]}',
        entitlement_id="book-first", user_id=7, origin_device_id=None,
    )
    second = spool.stage_patch(
        raw_body=b'{"updatedAnnotations":[{"id":"new-arrival"}]}',
        entitlement_id="book-second", user_id=7, origin_device_id=None,
    )

    assert first is not None
    assert first.path.exists(), "making room deleted the only copy of an unresolved PATCH"
    assert spool.load_spooled_patch(first.path)["dispatch_status"] == "staged"
    assert second is None, "the new record must fail open when only protected records remain"
    assert len(list(root.glob("patch-*.json.gz"))) == 1


@pytest.mark.unit
def test_failed_new_write_does_not_destroy_the_existing_recovery_record(
    monkeypatch, tmp_path,
):
    """Pruning cannot commit before the replacement record is durable."""
    spool, _root_path = _root(monkeypatch, tmp_path)
    monkeypatch.setattr(spool, "MAX_FILES", 1)
    monkeypatch.setattr(spool, "MAX_TOTAL_BYTES", 1024 * 1024)
    first = spool.stage_patch(
        raw_body=b'{"updatedAnnotations":[{"id":"only-copy"}]}',
        entitlement_id="book-first", user_id=7, origin_device_id=None,
    )
    assert first is not None
    assert first.mark_dispatch_outcome("dispatch_completed") is True

    def _fail_write(*_args, **_kwargs):
        raise OSError("simulated disk failure after pruning")

    monkeypatch.setattr(spool, "_replace_record_locked", _fail_write)
    second = spool.stage_patch(
        raw_body=b'{"updatedAnnotations":[{"id":"write-fails"}]}',
        entitlement_id="book-second", user_id=7, origin_device_id=None,
    )

    assert second is None
    assert first.path.exists(), "a failed spool write destructively committed its prune"
    assert spool.load_spooled_patch(first.path)["body"].endswith(b'"only-copy"}]}')


@pytest.mark.unit
def test_retention_schedule_failure_happens_before_record_commit(monkeypatch, tmp_path):
    """A post-write maintenance failure cannot create an unreported record."""
    spool, root = _root(monkeypatch, tmp_path)

    def _fail_schedule(*_args, **_kwargs):
        raise RuntimeError("simulated timer creation failure")

    monkeypatch.setattr(spool, "_schedule_retention", _fail_schedule)
    ticket = spool.stage_patch(
        raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )

    assert ticket is None
    assert list(root.glob("patch-*.json.gz")) == []


@pytest.mark.unit
def test_cross_process_lock_contention_fails_open_without_waiting(monkeypatch, tmp_path):
    """A busy peer must not block this gevent worker's entire request hub."""
    spool, root = _root(monkeypatch, tmp_path)
    root.mkdir(parents=True)
    context = multiprocessing.get_context("fork")
    ready_parent, ready_child = context.Pipe(duplex=False)
    release_child, release_parent = context.Pipe(duplex=False)
    holder = context.Process(
        target=_hold_advisory_lock,
        args=(root / ".spool.lock", ready_child, release_child),
    )
    holder.start()
    assert ready_parent.poll(5), "lock-holder process did not start"
    ready_parent.recv()

    result = []
    finished = threading.Event()

    def _stage():
        try:
            result.append(spool.stage_patch(
                raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
                user_id=7, origin_device_id=None,
            ))
        finally:
            finished.set()

    caller = threading.Thread(target=_stage, daemon=True)
    caller.start()
    completed_while_lock_was_busy = finished.wait(0.2)
    release_parent.send(True)
    caller.join(5)
    holder.join(5)

    assert not caller.is_alive()
    assert not holder.is_alive()
    assert completed_while_lock_was_busy, (
        "blocking flock waited on another process; in production that stalls "
        "every greenlet in this worker's gevent hub"
    )
    assert result == [None]


@pytest.mark.unit
def test_busy_spool_rejects_new_work_immediately(monkeypatch, tmp_path):
    """A wedged worker opens the circuit instead of accumulating more work."""
    spool, root = _root(monkeypatch, tmp_path)
    assert spool._REQUEST_IO_GATE.acquire(blocking=False)
    try:
        started = time.monotonic()
        ticket = spool.stage_patch(
            raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
            user_id=7, origin_device_id=None,
        )
        elapsed = time.monotonic() - started
    finally:
        spool._REQUEST_IO_GATE.release()

    assert ticket is None
    assert elapsed < 0.05
    assert not root.exists()


@pytest.mark.unit
def test_outcome_rewrite_cannot_grow_spool_past_total_byte_bound(monkeypatch, tmp_path):
    """The cap applies after status rewrites, not only after initial staging."""
    spool, _root_path = _root(monkeypatch, tmp_path)
    ticket = spool.stage_patch(
        raw_body=bytes(range(256)) * 16, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )
    assert ticket is not None
    staged_record = spool._load_disk_record(ticket.path)
    staged_size = ticket.path.stat().st_size

    fixed_now = spool.datetime.fromisoformat(staged_record["dispatch_updated_at"])
    monkeypatch.setattr(
        spool, "datetime", SimpleNamespace(now=lambda _timezone: fixed_now),
    )
    exception_record = dict(staged_record)
    exception_record["dispatch_status"] = "dispatch_exception"
    assert len(spool._compress(exception_record)) > staged_size
    monkeypatch.setattr(spool, "MAX_TOTAL_BYTES", staged_size)

    ticket.mark_dispatch_outcome("dispatch_exception")

    assert ticket.path.stat().st_size <= spool.MAX_TOTAL_BYTES


@pytest.mark.unit
def test_expired_record_is_removed_without_requiring_another_patch(monkeypatch, tmp_path):
    """A quiet spool must not retain private annotation text past 14 days."""
    spool, _root_path = _root(monkeypatch, tmp_path)
    ticket = spool.stage_patch(
        raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )
    assert ticket is not None
    expired = spool.time.time() - spool.MAX_AGE_SECONDS - 1
    os.utime(ticket.path, (expired, expired))

    assert list(spool.iter_replay_candidates()) == []
    assert not ticket.path.exists()


@pytest.mark.unit
def test_age_retention_runs_while_spool_has_no_new_traffic(monkeypatch, tmp_path):
    """The deadline worker, not replay enumeration, enforces quiet-spool age."""
    spool, _root_path = _root(monkeypatch, tmp_path)
    monkeypatch.setattr(spool, "MAX_AGE_SECONDS", 0.05)
    ticket = spool.stage_patch(
        raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )
    assert ticket is not None

    deadline = time.monotonic() + 1.0
    while ticket.path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert not ticket.path.exists(), "retention still depended on later spool traffic"


@pytest.mark.unit
def test_startup_retention_expires_records_before_any_new_patch(monkeypatch, tmp_path):
    """A restarted process schedules existing records without request traffic."""
    spool, _root_path = _root(monkeypatch, tmp_path)
    ticket = spool.stage_patch(
        raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )
    assert ticket is not None
    expired = spool.time.time() - spool.MAX_AGE_SECONDS - 1
    os.utime(ticket.path, (expired, expired))
    monkeypatch.setattr(spool, "_RETENTION_STARTED", False)

    assert spool.start_retention_maintenance() is True
    deadline = time.monotonic() + 1.0
    while ticket.path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert not ticket.path.exists(), "startup did not enforce age without a PATCH"


@pytest.mark.unit
def test_first_record_fsyncs_each_new_directory_entry(monkeypatch, tmp_path):
    """First-use durability requires fsyncing the newly created directory chain."""
    spool = _module()
    root = tmp_path / "private-parent" / "kobo-patch-spool"
    monkeypatch.setattr(spool, "_spool_root", lambda: root)
    real_fsync = spool.os.fsync
    fsynced_inodes = set()

    def _track_fsync(fd):
        fsynced_inodes.add(os.fstat(fd).st_ino)
        return real_fsync(fd)

    monkeypatch.setattr(spool.os, "fsync", _track_fsync)
    ticket = spool.stage_patch(
        raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )

    assert ticket is not None
    assert root.stat().st_ino in fsynced_inodes
    assert root.parent.stat().st_ino in fsynced_inodes, (
        "the spool directory entry was never fsynced in its parent"
    )
    assert tmp_path.stat().st_ino in fsynced_inodes, (
        "the private-parent directory entry was never fsynced in CONFIG_DIR"
    )


@pytest.mark.unit
def test_replay_candidate_predicate_distinguishes_completed_from_lost():
    spool = _module()
    assert spool.is_replay_candidate("staged") is True
    assert spool.is_replay_candidate("dispatch_exception") is True
    assert spool.is_replay_candidate("dispatch_completed") is False


@pytest.mark.unit
def test_oversized_patch_is_not_partially_spooled(monkeypatch, tmp_path):
    spool, root = _root(monkeypatch, tmp_path)
    monkeypatch.setattr(spool, "MAX_BODY_BYTES", 8)

    ticket = spool.stage_patch(
        raw_body=b"123456789", entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )

    assert ticket is None
    assert not list(root.glob("patch-*.json.gz")) if root.exists() else True


@pytest.mark.unit
def test_private_observability_root_is_git_ignored():
    spool = _module()
    private_parent = spool._spool_root().parent.name
    patterns = {
        line.strip()
        for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    accepted = {
        f"/{private_parent}/", f"/{private_parent}",
        f"{private_parent}/", private_parent,
    }
    assert patterns & accepted, (
        f"{private_parent!r} can contain raw annotation text and must be git-ignored"
    )


@pytest.mark.unit
def test_private_observability_root_is_excluded_from_docker_context():
    spool = _module()
    private_parent = spool._spool_root().parent.name
    patterns = {
        line.strip().rstrip("/")
        for line in (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert private_parent in patterns, (
        f"{private_parent!r} can contain raw annotation text and must not enter images"
    )


@pytest.mark.unit
def test_unreadable_patch_body_still_refuses_but_unreadable_get_body_does_not(
    monkeypatch, tmp_path,
):
    """Moving the body read earlier must not change either hazard.

    The read moved out of the PATCH try-block so the exchange capture could see
    the bytes.  Two things must survive that move:
      * a PATCH whose body cannot be read is still refused with 503, because
        nothing was stored (F-5c1146 / #1825);
      * a GET whose body cannot be read is NOT refused, because a 503 on the
        annotations GET is a measured way to make Nickel empty the book's local
        annotation set.
    """
    _root(monkeypatch, tmp_path)
    app = _app(monkeypatch, dispatch=lambda *_a, **_k: None)

    def _unreadable(self, *args, **kwargs):
        del self, args, kwargs
        raise RuntimeError("body read exploded")

    monkeypatch.setattr(app.request_class, "get_data", _unreadable)

    patch_response = app.test_client().patch(
        f"/annotations/{BOOK_UUID}", data=RAW_PATCH, content_type="application/json",
    )
    assert patch_response.status_code == 503

    get_response = app.test_client().get(f"/annotations/{BOOK_UUID}")
    assert get_response.status_code != 503
