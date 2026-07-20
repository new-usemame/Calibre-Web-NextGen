# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#920 / #699 — the remote annotation fan-out must not run on the request path.

A sync-target push is a blocking HTTPS call with a 10s timeout. CWNG serves
requests with gevent and does NOT monkey-patch, so running it on the request
greenlet froze the WHOLE application: measured on cwn-local, three highlights
took 30.25s to push and an unrelated anonymous ``GET /login`` blocked for
28.17s against a 0.041s idle baseline. The KOReader plugin (15s timeout) then
reported "Server push failed" for syncs the server had saved, and the 3s
Docker healthcheck restarted the container.

These tests pin the fix: with background dispatch wired, the request path
persists locally, marks the target ``pending`` and returns without touching
the remote; the queued job performs the push later.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.services import annotation_sync
from cps.services.annotation_sync import (
    dispatch_annotation_deletes,
    dispatch_annotation_sync,
    dispatch_existing_annotation_sync,
    execute_jobs,
    register_handler,
    reset_registry_for_testing,
    set_remote_enqueue,
)
from cps.services.annotation_sync.base import AnnotationSyncTargetHandler, SyncResult


class SlowHandler(AnnotationSyncTargetHandler):
    """Stands in for the real blocking HTTPS call."""

    target_name = "stub"

    def __init__(self, delay=0.0):
        self.delay = delay
        self.calls = []

    def is_enabled(self, user):
        return True

    def push(self, annotation, book, user, payload=None):
        time.sleep(self.delay)
        self.calls.append(("push", annotation.annotation_id))
        return SyncResult(status="synced", target_record_id="r1")

    def delete(self, sync_target, user):
        time.sleep(self.delay)
        self.calls.append(("delete", sync_target.target_record_id))
        return SyncResult(status="tombstone", target_record_id="r1")


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_registry_for_testing()
    set_remote_enqueue(None)
    yield
    reset_registry_for_testing()
    set_remote_enqueue(None)


@pytest.fixture
def patched_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.execute(text("PRAGMA foreign_keys=ON"))
    user = ub.User(name="u", email="u@e.com", role=0, password="x", hardcover_token="t")
    s.add(user)
    s.commit()
    monkeypatch.setattr(ub, "session", s)
    monkeypatch.setattr(ub, "session_commit", lambda: s.commit())
    yield s, user
    s.close()


@pytest.fixture
def queue():
    """Install a capturing enqueue hook; returns the collected job list."""
    collected = []

    def _enqueue(user, jobs):
        collected.extend(jobs)

    set_remote_enqueue(_enqueue)
    return collected


def _payload(annotation_id, text_="hi"):
    return {
        "id": annotation_id,
        "highlightedText": text_,
        "highlightColor": "yellow",
        "location": {"span": {"chapterProgress": 0.5}},
    }


def _book(book_id=7):
    return SimpleNamespace(id=book_id, title=f"Book {book_id}")


def _run(session, user, jobs, book):
    execute_jobs(session, user, jobs, book_loader=lambda _bid: book)
    session.commit()


def test_push_does_not_touch_the_remote_on_the_request_path(patched_session, queue):
    s, user = patched_session
    handler = SlowHandler()
    register_handler(handler)

    dispatch_annotation_sync([_payload("uuid-a")], _book(), user)

    assert handler.calls == [], "remote push ran on the request path"
    ann = s.query(ub.Annotation).one()
    assert ann.highlighted_text == "hi", "annotation must still persist locally"
    target = s.query(ub.AnnotationSyncTarget).one()
    assert target.status == "pending"
    assert queue == [{"op": "push", "annotation": ann.id, "book": 7,
                      "payload": _payload("uuid-a")}]


def test_queued_push_reaches_the_remote_and_flips_status(patched_session, queue):
    s, user = patched_session
    handler = SlowHandler()
    register_handler(handler)
    book = _book()

    dispatch_annotation_sync([_payload("uuid-a")], book, user)
    _run(s, user, queue, book)

    assert handler.calls == [("push", "uuid-a")]
    target = s.query(ub.AnnotationSyncTarget).one()
    assert target.status == "synced"
    assert target.target_record_id == "r1"


def test_request_path_does_not_wait_for_a_slow_remote(patched_session, queue):
    """The measured symptom: N annotations x ~10s of frozen app. The dispatch
    call must return in ~no time regardless of how slow the target is."""
    s, user = patched_session
    register_handler(SlowHandler(delay=0.4))

    started = time.monotonic()
    dispatch_annotation_sync(
        [_payload("uuid-a"), _payload("uuid-b"), _payload("uuid-c")], _book(), user,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 0.4, f"request path blocked on the remote for {elapsed:.2f}s"
    assert len(queue) == 3


def test_existing_annotation_push_is_queued(patched_session, queue):
    s, user = patched_session
    handler = SlowHandler()
    register_handler(handler)
    book = _book()
    ann = ub.Annotation(user_id=user.id, annotation_id="uuid-x", book_id=book.id,
                        source="koreader", highlighted_text="hi")
    s.add(ann)
    s.commit()

    dispatch_existing_annotation_sync(ann, book, user)

    assert handler.calls == []
    assert s.query(ub.AnnotationSyncTarget).one().status == "pending"

    _run(s, user, queue, book)
    assert handler.calls == [("push", "uuid-x")]
    assert s.query(ub.AnnotationSyncTarget).one().status == "synced"


def test_delete_hides_locally_immediately_and_queues_the_remote(patched_session, queue):
    s, user = patched_session
    handler = SlowHandler()
    register_handler(handler)
    book = _book()

    dispatch_annotation_sync([_payload("uuid-a")], book, user)
    _run(s, user, queue, book)
    queue.clear()
    handler.calls.clear()

    dispatch_annotation_deletes(["uuid-a"], user, book_id=book.id)

    ann = s.query(ub.Annotation).one()
    assert ann.hidden is True, "local soft-delete must not wait on the remote"
    assert handler.calls == [], "remote delete ran on the request path"
    target = s.query(ub.AnnotationSyncTarget).one()
    assert queue == [{"op": "delete", "sync_target": target.id}]

    _run(s, user, queue, book)
    assert handler.calls == [("delete", "r1")]
    assert s.query(ub.AnnotationSyncTarget).one().status == "tombstone"


def test_tombstoned_target_is_never_requeued(patched_session, queue):
    s, user = patched_session
    handler = SlowHandler()
    register_handler(handler)
    book = _book()
    dispatch_annotation_sync([_payload("uuid-a")], book, user)
    _run(s, user, queue, book)
    dispatch_annotation_deletes(["uuid-a"], user, book_id=book.id)
    _run(s, user, queue, book)
    queue.clear()

    dispatch_annotation_sync([_payload("uuid-a", text_="back")], book, user)

    assert queue == [], "a tombstoned target must stay terminal"
    assert s.query(ub.AnnotationSyncTarget).one().status == "tombstone"


def test_enqueue_failure_falls_back_to_inline(patched_session):
    """Losing the queue must not lose the sync — correctness beats latency."""
    s, user = patched_session
    handler = SlowHandler()
    register_handler(handler)

    def _boom(user_, jobs):
        raise RuntimeError("worker is down")

    set_remote_enqueue(_boom)
    dispatch_annotation_sync([_payload("uuid-a")], _book(), user)

    assert handler.calls == [("push", "uuid-a")]
    assert s.query(ub.AnnotationSyncTarget).one().status == "synced"


def test_without_the_hook_the_dispatch_stays_synchronous(patched_session):
    """Embeddings without a WorkerThread (and every existing unit test) keep
    the original inline semantics."""
    s, user = patched_session
    handler = SlowHandler()
    register_handler(handler)

    dispatch_annotation_sync([_payload("uuid-a")], _book(), user)

    assert handler.calls == [("push", "uuid-a")]
    assert s.query(ub.AnnotationSyncTarget).one().status == "synced"


def test_execute_jobs_survives_a_failing_job(patched_session, queue):
    s, user = patched_session

    class Exploding(SlowHandler):
        def push(self, annotation, book, user, payload=None):
            if annotation.annotation_id == "uuid-a":
                raise RuntimeError("hardcover 500")
            return super().push(annotation, book, user, payload)

    handler = Exploding()
    register_handler(handler)
    book = _book()
    dispatch_annotation_sync([_payload("uuid-a"), _payload("uuid-b")], book, user)
    _run(s, user, queue, book)

    statuses = {
        t.annotation.annotation_id: t.status
        for t in s.query(ub.AnnotationSyncTarget).all()
    }
    assert statuses == {"uuid-a": "failed", "uuid-b": "synced"}


def test_hardcover_handler_rebinds_its_blacklist_lookup_to_the_task_session():
    """The worker runs on its own thread with its own app.db session; the
    global ub.session is not thread-safe, so the handler must not read
    through it."""
    from cps.services.annotation_sync.hardcover import HardcoverHandler

    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    other = sessionmaker(bind=engine)()
    other.add(ub.HardcoverBookBlacklist(book_id=42, blacklist_annotations=True))
    other.commit()

    bound = HardcoverHandler().for_session(other)
    assert bound._blacklist_check(42) is True
    assert bound._blacklist_check(43) is False
    other.close()


def test_background_dispatch_wires_the_worker_task():
    """enable_background_dispatch() must install the real task enqueue."""
    from cps.tasks.annotation_sync import enqueue_annotation_sync

    annotation_sync.enable_background_dispatch()
    assert annotation_sync._background_enqueue() is enqueue_annotation_sync
