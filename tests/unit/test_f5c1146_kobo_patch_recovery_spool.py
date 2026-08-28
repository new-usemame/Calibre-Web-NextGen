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
from gevent import reinit as gevent_reinit

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


def _wait_for_full_request_io_capacity(spool, timeout=2):
    """Probe and restore the exact two-slot capacity after background work."""
    deadline = time.monotonic() + timeout
    capacity = []
    while time.monotonic() < deadline:
        capacity = [
            spool._REQUEST_IO_SLOTS.acquire(blocking=False)
            for _index in range(spool.MAX_PENDING_IO_OPERATIONS + 1)
        ]
        for acquired in capacity:
            if acquired:
                spool._REQUEST_IO_SLOTS.release()
        if capacity == [True, True, False]:
            return capacity
        time.sleep(0.01)
    return capacity


def _require_full_request_io_capacity(spool, boundary):
    """Fail the permit owner, then restore isolation for the next test."""
    capacity = _wait_for_full_request_io_capacity(spool)
    if capacity == [True, True, False]:
        return
    spool._reset_request_io_slots_after_fork()
    pytest.fail(
        "Kobo PATCH request I/O permit leak "
        f"{boundary}: expected [True, True, False], observed {capacity}; "
        "resetting capacity so later tests are not poisoned"
    )


@pytest.fixture(autouse=True)
def _isolate_request_io_capacity():
    """Attribute permit leaks to their owner instead of the next test."""
    spool = _module()
    _require_full_request_io_capacity(spool, "before test setup")
    yield
    _require_full_request_io_capacity(spool, "during test teardown")


@pytest.fixture(autouse=True)
def _cancel_spool_retention_timers_after_test():
    """Secondary test isolation; production dependency capture is the fix."""
    yield
    spool = _module()
    with spool._RETENTION_TIMERS_LOCK:
        timers = [timer for _deadline, timer in spool._RETENTION_TIMERS.values()]
        spool._RETENTION_TIMERS.clear()
        spool._RETENTION_STARTED = False
    for timer in timers:
        timer.cancel()


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


def _stage_in_forked_child(root, result):
    """Stage once in a forked child and report the durable body."""
    gevent_reinit()
    spool = _module()
    spool._spool_root = lambda: Path(root)
    process_lock_inherited_locked = spool._PROCESS_LOCK.locked()
    spool.stage_patch(
        raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )
    deadline = time.monotonic() + 2
    paths = []
    while not paths and time.monotonic() < deadline:
        paths = list(Path(root).glob("patch-*.json.gz"))
        if not paths:
            time.sleep(0.01)
    body = spool.load_spooled_patch(paths[0])["body"] if paths else None

    capacity = _wait_for_full_request_io_capacity(spool)
    result.send((body, capacity, process_lock_inherited_locked))


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
        lambda **_kwargs: pytest.fail("owned PATCH must not contact Kobo"),
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

    assert response.status_code == 204
    assert response.get_data() == b""
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

    Spooling makes an unresolved delta recoverable server-side. It does not
    prove the whole PATCH was stored: SQLite savepoint writes may survive a
    later rollback, while other members may still be missing. CWNG must answer
    503 rather than let the device retire a delta that needs reconciliation.
    Asserting 207 here would silently revert F-5c1146.
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
def test_parse_exception_still_leaves_replay_candidate(monkeypatch, tmp_path):
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

    # Same contract as the dispatch-exception case: the body is recoverable,
    # but complete persistence is unproven, so the device must not be told the
    # delta landed.
    assert response.status_code == 503
    [(_path, record)] = _records(spool, root)
    assert record["body"] == RAW_PATCH
    assert _wait_for_full_request_io_capacity(spool) == [True, True, False]
    assert record["dispatch_status"] == "dispatch_exception"


@pytest.mark.unit
def test_finalizer_marks_ticket_when_view_raises_past_exception_handler(
    monkeypatch, tmp_path,
):
    spool, root = _root(monkeypatch, tmp_path)
    app = _app(monkeypatch, dispatch=lambda *_args, **_kwargs: None)

    class EscapesViewHandler(BaseException):
        pass

    def _escape(_content_id):
        raise EscapesViewHandler("outside the view's Exception handler")

    monkeypatch.setattr(rs, "resolve_entitlement_ownership", _escape)
    with app.test_request_context(
        f"/annotations/{BOOK_UUID}",
        method="PATCH",
        data=RAW_PATCH,
        content_type="application/json",
    ):
        with pytest.raises(EscapesViewHandler):
            rs.handle_annotations.__wrapped__(BOOK_UUID)

    [(path, record)] = _records(spool, root)
    assert record["body"] == RAW_PATCH
    assert record["dispatch_status"] == "dispatch_exception"
    assert list(spool.iter_replay_candidates()) == [path]


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

    assert response.status_code == 204
    assert response.get_data() == b""
    assert len(dispatched) == 1


@pytest.mark.unit
def test_existing_ownership_unknown_503_is_unchanged_and_body_remains_replayable(
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

    for _attempt in range(5):
        response = app.test_client().patch(
            f"/annotations/{BOOK_UUID}",
            data=RAW_PATCH,
            content_type="application/json",
        )
        assert response.status_code == 503
        assert response.get_json() == {
            "error": "Annotation capture temporarily unavailable",
        }

    records = _records(spool, root)
    assert len(records) == 1, "one unchanged body must occupy one replay slot"
    [(_path, record)] = records
    assert record["body"] == RAW_PATCH
    assert record["dispatch_status"] == "dispatch_refused"
    assert spool.is_replay_candidate(record["dispatch_status"]) is True
    assert record["attempt_count"] == 5


@pytest.mark.unit
def test_unpersisted_retry_loop_deduplicates_and_cannot_starve_another_user(
    monkeypatch, tmp_path,
):
    spool, root = _root(monkeypatch, tmp_path)
    monkeypatch.setattr(spool, "MAX_FILES", 3)
    monkeypatch.setattr(spool, "MAX_TOTAL_BYTES", 1024 * 1024)
    app = _app(monkeypatch, dispatch=lambda *_args, **_kwargs: False)

    for _attempt in range(5):
        response = app.test_client().patch(
            f"/annotations/{BOOK_UUID}",
            data=RAW_PATCH,
            content_type="application/json",
        )
        assert response.status_code == 503

    first_user_records = _records(spool, root)
    assert len(first_user_records) == 1
    assert first_user_records[0][1]["attempt_count"] == 5
    assert first_user_records[0][1]["dispatch_status"] == "dispatch_refused"

    other_body = b'{"updatedAnnotations":[{"id":"other-user"}]}'
    other_user = spool.stage_patch(
        raw_body=other_body,
        entitlement_id="other-book",
        user_id=99,
        origin_device_id="other-device",
    )

    assert other_user is not None, "one client's retries starved another tenant"
    records = _records(spool, root)
    assert len(records) == 2
    assert {(record["user_id"], record["body"]) for _path, record in records} == {
        (7, RAW_PATCH),
        (99, other_body),
    }


@pytest.mark.unit
def test_retry_identity_includes_scope_and_exact_body(monkeypatch, tmp_path):
    spool, root = _root(monkeypatch, tmp_path)

    first = spool.stage_patch(
        raw_body=RAW_PATCH,
        entitlement_id=BOOK_UUID,
        user_id=7,
        origin_device_id="device-a",
    )
    exact_retry = spool.stage_patch(
        raw_body=RAW_PATCH,
        entitlement_id=BOOK_UUID,
        user_id=7,
        origin_device_id="device-a",
    )
    other_user = spool.stage_patch(
        raw_body=RAW_PATCH,
        entitlement_id=BOOK_UUID,
        user_id=99,
        origin_device_id="device-a",
    )
    other_body = spool.stage_patch(
        raw_body=RAW_PATCH + b" ",
        entitlement_id=BOOK_UUID,
        user_id=7,
        origin_device_id="device-a",
    )

    assert all(ticket is not None for ticket in (first, exact_retry, other_user, other_body))
    assert exact_retry.spool_id == first.spool_id
    assert other_user.spool_id != first.spool_id
    assert other_body.spool_id != first.spool_id
    assert len(_records(spool, root)) == 3


@pytest.mark.unit
def test_same_identity_reuses_completed_record_for_a_later_attempt(
    monkeypatch, tmp_path,
):
    spool, root = _root(monkeypatch, tmp_path)
    first = spool.stage_patch(
        raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )
    assert first is not None
    assert first.mark_dispatch_outcome("dispatch_completed") is True

    retry = spool.stage_patch(
        raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )

    assert retry is not None
    assert retry.spool_id == first.spool_id
    [(_path, record)] = _records(spool, root)
    assert record["dispatch_status"] == "staged"
    assert record["attempt_count"] == 2


@pytest.mark.unit
def test_retry_consolidates_legacy_duplicate_records(monkeypatch, tmp_path):
    spool, root = _root(monkeypatch, tmp_path)
    root.mkdir(parents=True)
    now = spool.datetime.now(spool.timezone.utc).isoformat()
    encoded = spool.base64.b64encode(RAW_PATCH).decode("ascii")
    for index in range(3):
        record = {
            "schema_version": 1,
            "spool_id": f"{index:032x}",
            "received_at": now,
            "entitlement_id": BOOK_UUID,
            "user_id": 7,
            "origin_device_id": None,
            "body_encoding": "base64",
            "body_length": len(RAW_PATCH),
            "body_sha256": spool.sha256_bytes(RAW_PATCH),
            "body_base64": encoded,
            "dispatch_status": "dispatch_exception",
            "dispatch_updated_at": now,
        }
        path = root / (
            f"patch-{index:020d}-{record['spool_id']}-dispatch_exception.json.gz"
        )
        spool._replace_record_locked(path, spool._compress(record))

    retry = spool.stage_patch(
        raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )

    assert retry is not None
    [(path, record)] = _records(spool, root)
    assert spool._identity_from_path(path) == record["replay_identity_sha256"]
    assert record["attempt_count"] == 4
    assert record["dispatch_status"] == "staged"


@pytest.mark.unit
def test_older_overlapping_retry_cannot_overwrite_newer_outcome(monkeypatch, tmp_path):
    spool, root = _root(monkeypatch, tmp_path)
    first = spool.stage_patch(
        raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )
    second = spool.stage_patch(
        raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )

    assert first is not None and second is not None
    assert first.spool_id == second.spool_id
    assert first.mark_dispatch_outcome("dispatch_exception") is False
    assert second.mark_dispatch_outcome("dispatch_completed") is True
    [(_path, record)] = _records(spool, root)
    assert record["dispatch_status"] == "dispatch_completed"


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
    elapsed = []
    finished = threading.Event()

    def _stage():
        started = time.monotonic()
        try:
            result.append(spool.stage_patch(
                raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
                user_id=7, origin_device_id=None,
            ))
        finally:
            elapsed.append(time.monotonic() - started)
            finished.set()

    caller = threading.Thread(target=_stage, daemon=True)
    caller.start()
    grace = spool.REQUEST_IO_TIMEOUT_SECONDS * 5
    completed_while_lock_was_busy = finished.wait(grace)
    release_parent.send(True)
    caller.join(5)
    holder.join(5)

    assert not caller.is_alive()
    assert not holder.is_alive()
    assert completed_while_lock_was_busy, (
        "the production spool deadline allowed a blocked flock to hold the "
        f"request for at least {grace:.1f}s"
    )
    assert spool.REQUEST_IO_TIMEOUT_SECONDS == 1.0
    assert elapsed[0] < grace
    assert result == [None]
    deadline = time.monotonic() + 2
    while not list(root.glob("patch-*.json.gz")) and time.monotonic() < deadline:
        time.sleep(0.01)
    [(_path, record)] = _records(spool, root)
    assert record["body"] == RAW_PATCH


@pytest.mark.unit
def test_forked_child_recovers_full_request_io_capacity(monkeypatch, tmp_path):
    """Parent-only permit owners cannot poison a live-forked child."""
    spool, root = _root(monkeypatch, tmp_path)
    release = threading.Event()
    ready = [threading.Event(), threading.Event()]

    def _hold_slot(signal):
        slots = spool._REQUEST_IO_SLOTS
        acquired = slots.acquire(blocking=False)
        try:
            assert acquired
            signal.set()
            assert release.wait(5)
        finally:
            if acquired:
                slots.release()

    holders = [
        threading.Thread(target=_hold_slot, args=(signal,), daemon=True)
        for signal in ready
    ]
    for holder in holders:
        holder.start()
    child = None
    try:
        assert all(signal.wait(2) for signal in ready), (
            "Kobo PATCH request I/O permit leak prevented both parent holders "
            "from acquiring capacity"
        )

        context = multiprocessing.get_context("fork")
        result_parent, result_child = context.Pipe(duplex=False)
        child = context.Process(
            target=_stage_in_forked_child, args=(root, result_child),
        )
        child.start()
        assert result_parent.poll(5), "forked child did not report its stage"
        body, capacity, process_lock_inherited_locked = result_parent.recv()
    finally:
        release.set()
        if child is not None:
            child.join(5)
        for holder in holders:
            holder.join(5)

    assert not child.is_alive()
    assert all(not holder.is_alive() for holder in holders)
    assert process_lock_inherited_locked is False
    assert body == RAW_PATCH
    assert capacity == [True, True, False]


@pytest.mark.unit
def test_spool_uses_the_hub_owned_threadpool(monkeypatch, tmp_path):
    """Spool calls cannot create a dedicated pool with an unmanaged lifecycle."""
    spool, _root_path = _root(monkeypatch, tmp_path)
    hub_pool = spool.get_hub().threadpool
    pool_type = type(hub_pool)
    real_spawn = pool_type.spawn
    used_pools = []

    def _track_spawn(pool, *args, **kwargs):
        used_pools.append(pool)
        return real_spawn(pool, *args, **kwargs)

    monkeypatch.setattr(pool_type, "spawn", _track_spawn)
    ticket = spool.stage_patch(
        raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )

    assert ticket is not None
    assert used_pools == [hub_pool]


@pytest.mark.unit
def test_spawn_enqueue_then_raise_releases_the_permit_exactly_once(
    monkeypatch, tmp_path,
):
    """Caller and queued worker share one idempotent permit owner."""
    spool, root = _root(monkeypatch, tmp_path)
    hub_pool = spool.get_hub().threadpool
    pool_type = type(hub_pool)
    real_spawn = pool_type.spawn
    real_write = spool._write_or_reuse_record
    worker_started = threading.Event()
    release_worker = threading.Event()
    submitted = []

    def _stall_write(*args):
        worker_started.set()
        assert release_worker.wait(2)
        return real_write(*args)

    def _enqueue_then_raise(pool, *args, **kwargs):
        submitted.append(real_spawn(pool, *args, **kwargs))
        raise RuntimeError("spawn raised after enqueue")

    monkeypatch.setattr(spool, "_write_or_reuse_record", _stall_write)
    monkeypatch.setattr(pool_type, "spawn", _enqueue_then_raise)
    ticket = spool.stage_patch(
        raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )
    assert ticket is None
    assert worker_started.wait(2)
    release_worker.set()
    submitted[0].get(timeout=2)

    deadline = time.monotonic() + 2
    while not list(root.glob("patch-*.json.gz")) and time.monotonic() < deadline:
        time.sleep(0.01)
    [(_path, record)] = _records(spool, root)
    assert record["body"] == RAW_PATCH

    capacity = []
    try:
        capacity = [
            spool._REQUEST_IO_SLOTS.acquire(blocking=False)
            for _index in range(spool.MAX_PENDING_IO_OPERATIONS + 1)
        ]
        assert capacity == [True, True, False]
    finally:
        for acquired in capacity:
            if acquired:
                spool._REQUEST_IO_SLOTS.release()


@pytest.mark.unit
def test_timed_out_stage_finishes_and_does_not_poison_its_successor(
    monkeypatch, tmp_path,
):
    """The request deadline relinquishes the result, not the recovery bytes."""
    spool, root = _root(monkeypatch, tmp_path)
    monkeypatch.setattr(spool, "REQUEST_IO_TIMEOUT_SECONDS", 0.02)
    real_write = spool._write_or_reuse_record
    first_started = threading.Event()
    release_first = threading.Event()
    workers_finished = {
        "book-slow": threading.Event(),
        "book-next": threading.Event(),
    }

    def _stall_first(incoming, compressed, root, max_age_seconds):
        entitlement_id = incoming["entitlement_id"]
        try:
            if entitlement_id == "book-slow":
                first_started.set()
                assert release_first.wait(2), "test did not release the slow write"
            return real_write(incoming, compressed, root, max_age_seconds)
        finally:
            workers_finished[entitlement_id].set()

    monkeypatch.setattr(spool, "_write_or_reuse_record", _stall_first)
    slow_body = b'{"updatedAnnotations":[{"id":"slow-body"}]}'
    next_body = b'{"updatedAnnotations":[{"id":"next-body"}]}'

    started = time.monotonic()
    first = spool.stage_patch(
        raw_body=slow_body, entitlement_id="book-slow",
        user_id=7, origin_device_id=None,
    )
    first_elapsed = time.monotonic() - started
    assert first_started.is_set()

    started = time.monotonic()
    second = spool.stage_patch(
        raw_body=next_body, entitlement_id="book-next",
        user_id=7, origin_device_id=None,
    )
    second_elapsed = time.monotonic() - started
    release_first.set()

    assert first is None
    assert second is None or second.spool_id
    assert first_elapsed < 1.5 and second_elapsed < 1.5
    assert workers_finished["book-slow"].wait(2)
    assert workers_finished["book-next"].wait(2)
    bodies = {record["body"] for _path, record in _records(spool, root)}
    assert bodies == {slow_body, next_body}, (
        "the elapsed request deadline discarded a body or rejected the next "
        "stage while the first storage worker was still finishing"
    )
    assert _wait_for_full_request_io_capacity(spool) == [True, True, False]


@pytest.mark.unit
def test_pending_spool_work_is_bounded(monkeypatch, tmp_path):
    """Two pending operations cannot grow into an unbounded memory queue."""
    spool, root = _root(monkeypatch, tmp_path)
    acquired = []
    try:
        for _index in range(spool.MAX_PENDING_IO_OPERATIONS):
            permit = spool._REQUEST_IO_SLOTS.acquire(blocking=False)
            acquired.append(permit)
            assert permit
        started = time.monotonic()
        ticket = spool.stage_patch(
            raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
            user_id=7, origin_device_id=None,
        )
        elapsed = time.monotonic() - started
    finally:
        for permit in acquired:
            if permit:
                spool._REQUEST_IO_SLOTS.release()

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
def test_startup_retention_captures_root_and_age_before_thread_runs(
    monkeypatch, tmp_path,
):
    """A late bootstrap must retain the context that created its work."""
    spool = _module()
    spawning_root = tmp_path / "spawning-test-spool"
    next_test_root = tmp_path / "next-test-spool"
    spawning_root.mkdir()
    next_test_root.mkdir()

    def _seed_records(root, *, count, age_seconds):
        for index in range(count):
            path = root / f"patch-{index:020d}-dispatch_completed.json.gz"
            path.write_bytes(b"record contents are irrelevant to age expiry")
            mtime = spool.time.time() - age_seconds
            os.utime(path, (mtime, mtime))

    # The spawning root's record survives only if the original age bound is
    # captured. The next test's records are old enough to be deleted under
    # either bound, so they survive only if the original root is captured.
    _seed_records(spawning_root, count=1, age_seconds=1)
    _seed_records(next_test_root, count=3, age_seconds=120)
    active_root = [spawning_root]
    monkeypatch.setattr(spool, "_spool_root", lambda: active_root[0])
    monkeypatch.setattr(spool, "MAX_AGE_SECONDS", 60)
    monkeypatch.setattr(spool, "_RETENTION_STARTED", False)

    deferred_threads = []

    class _DeferredThread:
        def __init__(self, *, target, name, daemon, args=()):
            self.target = target
            self.args = args
            self.name = name
            self.daemon = daemon
            deferred_threads.append(self)

        def start(self):
            return None

        def run(self):
            self.target(*self.args)

    monkeypatch.setattr(spool, "threading", SimpleNamespace(Thread=_DeferredThread))
    monkeypatch.setattr(spool, "_schedule_retention", lambda *_args: None)

    assert spool.start_retention_maintenance() is True
    [bootstrap] = deferred_threads

    # Model pytest undoing the spawning test's patches and installing the next
    # test's different root and much shorter age bound before the OS runs the
    # daemon thread.
    active_root[0] = next_test_root
    monkeypatch.setattr(spool, "MAX_AGE_SECONDS", 0.05)
    bootstrap.run()

    assert len(list(spawning_root.glob("patch-*.json.gz"))) == 1, (
        "the bootstrap re-read the age bound after its caller moved on"
    )
    assert len(list(next_test_root.glob("patch-*.json.gz"))) == 3, (
        "the bootstrap expired records belonging to the next test"
    )


@pytest.mark.unit
def test_stage_patch_captures_root_and_age_before_storage_worker_runs(
    monkeypatch, tmp_path,
):
    """The gevent threadpool gets immutable path and retention inputs too."""
    spool = _module()
    spawning_root = tmp_path / "spawning-request-spool"
    next_root = tmp_path / "later-context-spool"
    active_root = [spawning_root]
    monkeypatch.setattr(spool, "_spool_root", lambda: active_root[0])
    monkeypatch.setattr(spool, "MAX_AGE_SECONDS", 60)
    scheduled = []
    monkeypatch.setattr(
        spool,
        "_schedule_retention",
        lambda root, deadline, max_age: scheduled.append(
            (Path(root), deadline, max_age)
        ),
    )

    def _run_after_context_changes(function, *args):
        active_root[0] = next_root
        monkeypatch.setattr(spool, "MAX_AGE_SECONDS", 0.05)
        return function(*args)

    monkeypatch.setattr(spool, "_run_off_hub_bounded", _run_after_context_changes)

    ticket = spool.stage_patch(
        raw_body=RAW_PATCH,
        entitlement_id=BOOK_UUID,
        user_id=7,
        origin_device_id=None,
    )

    assert ticket is not None
    assert ticket.path.parent == spawning_root
    assert not next_root.exists()
    [(scheduled_root, _deadline, scheduled_age)] = scheduled
    assert scheduled_root == spawning_root
    assert scheduled_age == 60


@pytest.mark.unit
def test_retention_timer_keeps_age_bound_from_when_it_was_scheduled(
    monkeypatch, tmp_path,
):
    """A timer firing later cannot inherit another context's age bound."""
    spool = _module()
    root = tmp_path / "timer-spool"
    root.mkdir()
    record = root / "patch-00000000000000000000-dispatch_completed.json.gz"
    record.write_bytes(b"record contents are irrelevant to age expiry")
    mtime = spool.time.time() - 1
    os.utime(record, (mtime, mtime))
    monkeypatch.setattr(spool, "_RETENTION_TIMERS", {})

    deferred_timers = []

    class _DeferredTimer:
        def __init__(self, interval, target, args=()):
            self.interval = interval
            self.target = target
            self.args = args
            self.daemon = False
            deferred_timers.append(self)

        def start(self):
            return None

        def cancel(self):
            return None

        def fire(self):
            self.target(*self.args)

    monkeypatch.setattr(spool, "threading", SimpleNamespace(Timer=_DeferredTimer))
    deadline = spool.time.time() + 59
    spool._schedule_retention(root, deadline, 60)
    first_timer = deferred_timers[0]

    monkeypatch.setattr(spool, "MAX_AGE_SECONDS", 0.05)
    first_timer.fire()

    assert record.exists(), "the timer re-read a shorter age bound when it fired"


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
    assert spool.is_replay_candidate("dispatch_refused") is True
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
    upstream_body = b'{"upstream":"preserved"}'
    monkeypatch.setattr(
        rs, "proxy_to_kobo_reading_services",
        lambda **_kwargs: app.response_class(
            upstream_body, status=207, headers={"X-Upstream": "same"},
        ),
    )

    def _unreadable(self, *args, **kwargs):
        del self, args, kwargs
        raise RuntimeError("body read exploded")

    monkeypatch.setattr(app.request_class, "get_data", _unreadable)

    patch_response = app.test_client().patch(
        f"/annotations/{BOOK_UUID}", data=RAW_PATCH, content_type="application/json",
    )
    assert patch_response.status_code == 503

    get_response = app.test_client().get(f"/annotations/{BOOK_UUID}")
    assert get_response.status_code == 207
    assert get_response.get_data() == upstream_body
    assert get_response.headers["X-Upstream"] == "same"


@pytest.mark.unit
@pytest.mark.parametrize("unresolved_status", ["staged", "dispatch_exception", "dispatch_refused"])
def test_full_spool_never_evicts_any_unresolved_status(
    monkeypatch, tmp_path, unresolved_status,
):
    """Eviction protection is pinned for EVERY unresolved status, not just `staged`.

    Raised by the cross-family review of #1861: making `dispatch_refused`
    evictable in `_select_victims` passed the entire suite, because the only
    protection test staged a record and left it `staged`. That is a one-line
    regression of this subsystem's headline invariant — a contributor
    "relieving spool pressure" would silently collect the only copy of a body
    the device still believes undelivered, which is the exact data-loss class
    the spool exists to prevent.

    `dispatch_refused` in particular must stay protected: it means the batch
    was refused with nothing persisted, so the body genuinely is a replay
    candidate (#1869).
    """
    spool, root = _root(monkeypatch, tmp_path)
    monkeypatch.setattr(spool, "MAX_FILES", 1)
    monkeypatch.setattr(spool, "MAX_TOTAL_BYTES", 1024 * 1024)

    first = spool.stage_patch(
        raw_body=b'{"updatedAnnotations":[{"id":"must-survive"}]}',
        entitlement_id="book-first", user_id=7, origin_device_id=None,
    )
    assert first is not None
    if unresolved_status != "staged":
        # mark_dispatch_outcome rewrites the record under a new status-bearing
        # filename and updates the ticket's own path.
        first.mark_dispatch_outcome(unresolved_status)
    surviving = first.path
    assert spool.load_spooled_patch(surviving)["dispatch_status"] == unresolved_status

    second = spool.stage_patch(
        raw_body=b'{"updatedAnnotations":[{"id":"new-arrival"}]}',
        entitlement_id="book-second", user_id=7, origin_device_id=None,
    )

    assert surviving.exists(), (
        f"a {unresolved_status} record was evicted to admit a different body; "
        "it is the only surviving copy of a PATCH the device believes delivered"
    )
    assert second is None, (
        "the new record must fail open while only unresolved records remain"
    )
    assert len(list(root.glob("patch-*.json.gz"))) == 1


@pytest.mark.unit
def test_retention_schedule_failure_happens_before_the_REUSE_record_commit(
    monkeypatch, tmp_path,
):
    """The same ordering, pinned at the reuse site rather than the new-record one.

    Raised by the cross-family review of #1861: `_write_or_reuse_record` has TWO
    `_install_record_locked` sites since the replay-identity rewrite, and the
    existing ordering test stages only once, so it instruments the new-record
    site alone. Moving `_schedule_retention` after the install at the *reuse*
    site passed the whole suite.

    The invariant is #1860's: a timer-creation failure must abort BEFORE the
    record commits, or the caller is told no recovery record exists while an
    unexpiring one sits on disk. A retry of an identical body takes the reuse
    path, so it needs its own pin.
    """
    spool, root = _root(monkeypatch, tmp_path)

    first = spool.stage_patch(
        raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )
    assert first is not None
    before = spool.load_spooled_patch(first.path)

    def _fail_schedule(*_args, **_kwargs):
        raise RuntimeError("simulated timer creation failure")

    monkeypatch.setattr(spool, "_schedule_retention", _fail_schedule)
    retry = spool.stage_patch(
        raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )

    assert retry is None, "a failed retention schedule must not yield a ticket"
    survivors = list(root.glob("patch-*.json.gz"))
    assert len(survivors) == 1, (
        "the reuse path committed a record despite the scheduler failing"
    )
    after = spool.load_spooled_patch(survivors[0])
    assert after["attempt_count"] == before["attempt_count"], (
        "the reuse path refreshed the record before its retention timer existed"
    )


@pytest.mark.unit
def test_a_filename_digest_match_does_not_merge_a_different_body(monkeypatch, tmp_path):
    """The filename digest is a lookup key; identity is decided on every field.

    Raised by the cross-family review of #1861: skipping the
    `_same_replay_identity` re-comparison and trusting the filename digest
    passed the whole suite. The merge keeps the EXISTING record's body, so a
    filename/content mismatch would silently discard the incoming one — and
    the PR advertises "every field is compared before merging".

    Only reachable through on-disk corruption that preserves a filename, or a
    SHA-256 collision. Pinned anyway, because the guard is one `if` and the
    thing it protects is the only copy of somebody's annotations.
    """
    import base64
    import gzip
    import json

    spool, root = _root(monkeypatch, tmp_path)

    first = spool.stage_patch(
        raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )
    assert first is not None

    # Corrupt the body in place, keeping the filename — and therefore the
    # identity digest the lookup keys on — exactly as it was.
    record = json.loads(gzip.decompress(first.path.read_bytes()))
    record["body_base64"] = base64.b64encode(b'{"updatedAnnotations":[{"id":"different"}]}').decode()
    first.path.write_bytes(spool._compress(record))

    retry = spool.stage_patch(
        raw_body=RAW_PATCH, entitlement_id=BOOK_UUID,
        user_id=7, origin_device_id=None,
    )

    assert retry is not None, "the retry must still be staged"
    assert len(list(root.glob("patch-*.json.gz"))) == 2, (
        "the retry merged into a record whose body differs — the filename "
        "digest was trusted instead of comparing the identity fields"
    )
