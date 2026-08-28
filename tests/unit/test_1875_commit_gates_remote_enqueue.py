# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Issue #1875 — rolled-back annotation writes must not reach the worker."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.services import annotation_sync
from cps.services.annotation_sync.base import AnnotationSyncTargetHandler, SyncResult


pytestmark = pytest.mark.unit

BOOK = SimpleNamespace(id=7, title="Commit Gate")
PAYLOAD = {
    "id": "uuid-a",
    "highlightedText": "hi",
    "highlightColor": "yellow",
    "noteText": None,
    "location": {"span": {"chapterProgress": 0.5}},
}


class StubHandler(AnnotationSyncTargetHandler):
    target_name = "stub"

    def is_enabled(self, user):
        return True

    def push(self, annotation, book, user, payload=None):
        return SyncResult(status="synced", target_record_id="remote-1")

    def delete(self, sync_target, user):
        return SyncResult(
            status="tombstone", target_record_id=sync_target.target_record_id,
        )


@pytest.fixture(autouse=True)
def _reset_dispatcher():
    annotation_sync.reset_registry_for_testing()
    annotation_sync.set_remote_enqueue(None)
    yield
    annotation_sync.reset_registry_for_testing()
    annotation_sync.set_remote_enqueue(None)


@pytest.fixture
def database(tmp_path, monkeypatch):
    from cps.services import annotation_backup

    annotation_backup.reset_for_tests()
    monkeypatch.setattr(annotation_backup, "WORKER_AUTOSTART", False)
    db_path = tmp_path / "ub.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"timeout": 30},
    )
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.execute(text("PRAGMA foreign_keys=ON"))
    user = ub.User(
        name="u", email="u@example.invalid", role=0, password="x",
        hardcover_token="token",
    )
    session.add(user)
    session.commit()
    monkeypatch.setattr(ub, "session", session)
    annotation_sync.register_handler(StubHandler())

    yield db_path, session, user

    session.close()
    engine.dispose()
    annotation_backup.reset_for_tests()


def _commit_success(session):
    def commit():
        session.commit()
        return True

    return commit


def _commit_failure(session):
    def commit():
        session.rollback()
        return False

    return commit


def _capture_jobs():
    enqueued = []
    annotation_sync.set_remote_enqueue(
        lambda _user, jobs: enqueued.extend(jobs),
    )
    return enqueued


def _seed_synced_annotation(session, user, monkeypatch):
    annotation_sync.set_remote_enqueue(None)
    monkeypatch.setattr(ub, "session_commit", _commit_success(session))
    annotation_sync.dispatch_annotation_sync([PAYLOAD], BOOK, user)
    annotation = session.query(ub.Annotation).one()
    target = session.query(ub.AnnotationSyncTarget).one()
    assert target.status == "synced"
    return annotation, target


def _seed_existing_annotation(session, user):
    annotation = ub.Annotation(
        user_id=user.id,
        book_id=BOOK.id,
        annotation_id="uuid-existing",
        source="webreader",
        highlighted_text="keep me",
        hidden=False,
        content_revision=3,
    )
    session.add(annotation)
    session.commit()
    return annotation


def _independent_row(db_path, statement):
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            row = connection.execute(text(statement)).one()
            return tuple(row)
    finally:
        engine.dispose()


def test_delete_commit_failure_does_not_enqueue_tombstone(
    database, monkeypatch, caplog,
):
    db_path, session, user = database
    _seed_synced_annotation(session, user, monkeypatch)
    enqueued = _capture_jobs()
    monkeypatch.setattr(ub, "session_commit", _commit_failure(session))
    caplog.set_level("ERROR", logger="cps.services.annotation_sync")

    result = annotation_sync.dispatch_annotation_deletes(
        ["uuid-a"], user, book_id=BOOK.id, deletable_sources={"kobo"},
    )
    persisted = _independent_row(
        db_path,
        "SELECT hidden, content_revision FROM annotation "
        "WHERE annotation_id = 'uuid-a'",
    )

    assert enqueued == [], "delete job reached remote enqueue after commit failure"
    assert persisted == (0, 1), "independent reader saw a rolled-back soft-delete"
    assert result is False
    assert (
        f"user={user.id} annotation_ids=['uuid-a'] job_count=1" in caplog.text
    )


def test_existing_sync_commit_failure_does_not_enqueue_push(
    database, monkeypatch, caplog,
):
    db_path, session, user = database
    annotation = _seed_existing_annotation(session, user)
    enqueued = _capture_jobs()
    monkeypatch.setattr(ub, "session_commit", _commit_failure(session))
    caplog.set_level("ERROR", logger="cps.services.annotation_sync")

    result = annotation_sync.dispatch_existing_annotation_sync(annotation, BOOK, user)
    persisted = _independent_row(
        db_path,
        "SELECT hidden, content_revision "
        "FROM annotation WHERE annotation_id = 'uuid-existing'",
    )

    assert enqueued == [], "push job reached remote enqueue after commit failure"
    assert persisted == (0, 3), "independent reader saw a changed annotation row"
    assert result is False
    assert (
        f"user={user.id} annotation_id=uuid-existing job_count=1" in caplog.text
    )


def test_delete_commit_success_enqueues_tombstone_exactly_once(
    database, monkeypatch,
):
    db_path, session, user = database
    _annotation, target = _seed_synced_annotation(session, user, monkeypatch)
    target_id = target.id
    enqueued = _capture_jobs()
    monkeypatch.setattr(ub, "session_commit", _commit_success(session))

    result = annotation_sync.dispatch_annotation_deletes(
        ["uuid-a"], user, book_id=BOOK.id, deletable_sources={"kobo"},
    )

    assert enqueued == [{"op": "delete", "sync_target": target_id}]
    assert _independent_row(
        db_path,
        "SELECT hidden, content_revision FROM annotation "
        "WHERE annotation_id = 'uuid-a'",
    ) == (1, 2)
    assert result is True


def test_existing_sync_commit_success_enqueues_push_exactly_once(
    database, monkeypatch,
):
    db_path, session, user = database
    annotation = _seed_existing_annotation(session, user)
    annotation_id = annotation.id
    enqueued = _capture_jobs()
    monkeypatch.setattr(ub, "session_commit", _commit_success(session))

    result = annotation_sync.dispatch_existing_annotation_sync(annotation, BOOK, user)

    assert enqueued == [{
        "op": "push", "annotation": annotation_id, "book": BOOK.id,
    }]
    assert _independent_row(
        db_path,
        "SELECT hidden, content_revision, "
        "(SELECT COUNT(*) FROM annotation_sync_target WHERE status = 'pending') "
        "FROM annotation WHERE annotation_id = 'uuid-existing'",
    ) == (0, 3, 1)
    assert result is True
