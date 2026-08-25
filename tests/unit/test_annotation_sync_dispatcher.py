# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dispatcher tests — UPSERT semantics, race handling, tombstone terminal."""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.services.annotation_sync import (
    register_handler,
    reset_registry_for_testing,
    dispatch_annotation_sync,
    dispatch_annotation_deletes,
)
from cps.services.annotation_sync.base import AnnotationSyncTargetHandler, SyncResult


class StubHandler(AnnotationSyncTargetHandler):
    target_name = "stub"

    def __init__(self, push_result=None, delete_result=None, enabled=True):
        self.push_result = push_result or SyncResult(status="synced", target_record_id="r1")
        self.delete_result = delete_result or SyncResult(status="tombstone", target_record_id="r1")
        self._enabled = enabled
        self.calls = []

    def is_enabled(self, user):
        return self._enabled

    def push(self, annotation, book, user, payload=None):
        self.calls.append(("push", annotation.annotation_id))
        return self.push_result

    def delete(self, sync_target, user):
        self.calls.append(("delete", sync_target.target_record_id))
        return self.delete_result


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_registry_for_testing()
    yield
    reset_registry_for_testing()


@pytest.fixture
def patched_session(monkeypatch):
    """Replace ub.session + ub.session_commit with a fresh in-memory session."""
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.execute(text("PRAGMA foreign_keys=ON"))
    user = ub.User(name="u", email="u@e.com", role=0, password="x", hardcover_token="t")
    s.add(user); s.commit()
    monkeypatch.setattr(ub, "session", s)
    monkeypatch.setattr(ub, "session_commit", lambda: s.commit())
    yield s, user
    s.close()


def _payload(annotation_id, text_="hi", color="yellow", note=None, progress=0.5):
    return {
        "id": annotation_id,
        "highlightedText": text_,
        "highlightColor": color,
        "noteText": note,
        "location": {"span": {"chapterProgress": progress}},
    }


def _book(book_id=7):
    from types import SimpleNamespace
    return SimpleNamespace(id=book_id, title=f"Book {book_id}")


def test_dispatch_creates_annotation_and_sync_target(patched_session):
    s, user = patched_session
    handler = StubHandler()
    register_handler(handler)
    dispatch_annotation_sync([_payload("uuid-a")], _book(), user)
    rows = s.query(ub.Annotation).all()
    assert len(rows) == 1
    assert rows[0].source == "kobo"
    targets = s.query(ub.AnnotationSyncTarget).all()
    assert len(targets) == 1
    assert targets[0].target == "stub"
    assert targets[0].status == "synced"
    assert targets[0].target_record_id == "r1"


def test_dispatch_updates_existing_annotation(patched_session):
    s, user = patched_session
    register_handler(StubHandler())
    dispatch_annotation_sync([_payload("uuid-a", text_="v1")], _book(), user)
    dispatch_annotation_sync([_payload("uuid-a", text_="v2")], _book(), user)
    rows = s.query(ub.Annotation).all()
    assert len(rows) == 1
    assert rows[0].highlighted_text == "v2"
    targets = s.query(ub.AnnotationSyncTarget).all()
    assert len(targets) == 1  # UPSERT, not duplicate


def test_dispatch_skips_disabled_handler(patched_session):
    s, user = patched_session
    h = StubHandler(enabled=False)
    register_handler(h)
    dispatch_annotation_sync([_payload("uuid-a")], _book(), user)
    assert s.query(ub.Annotation).count() == 1
    assert s.query(ub.AnnotationSyncTarget).count() == 0
    assert h.calls == []


def test_dispatch_records_failed_status(patched_session):
    s, user = patched_session
    h = StubHandler(push_result=SyncResult(status="failed", error_message="boom"))
    register_handler(h)
    dispatch_annotation_sync([_payload("uuid-a")], _book(), user)
    st = s.query(ub.AnnotationSyncTarget).one()
    assert st.status == "failed"
    assert st.error_message == "boom"
    assert st.last_synced is None


def test_dispatch_retry_clears_error_on_success(patched_session):
    s, user = patched_session
    class Flaky(AnnotationSyncTargetHandler):
        target_name = "stub"
        def __init__(self): self.n = 0
        def is_enabled(self, user): return True
        def push(self, a, b, u, payload=None):
            self.n += 1
            if self.n == 1:
                return SyncResult(status="failed", error_message="net")
            return SyncResult(status="synced", target_record_id="r1")
        def delete(self, st, u): return SyncResult(status="tombstone")
    register_handler(Flaky())
    p = _payload("uuid-a")
    dispatch_annotation_sync([p], _book(), user)
    dispatch_annotation_sync([p], _book(), user)
    st = s.query(ub.AnnotationSyncTarget).one()
    assert st.status == "synced"
    assert st.error_message is None
    assert st.target_record_id == "r1"


def test_dispatch_delete_transitions_to_tombstone(patched_session):
    s, user = patched_session
    register_handler(StubHandler())
    dispatch_annotation_sync([_payload("uuid-x")], _book(), user)
    assert s.query(ub.AnnotationSyncTarget).one().status == "synced"
    dispatch_annotation_deletes(["uuid-x"], user, deletable_sources={"kobo"})
    assert s.query(ub.AnnotationSyncTarget).one().status == "tombstone"


def test_kobo_delete_authority_refuses_foreign_sources_and_fans_out_owned(
    patched_session, caplog,
):
    """A Kobo delete may affect only rows a Kobo device could have held."""
    s, user = patched_session
    handler = StubHandler()
    register_handler(handler)
    dispatch_annotation_sync([_payload("kobo-owned")], _book(), user)

    for annotation_id, source in (
        ("webreader-foreign", "webreader"),
        ("koreader-foreign", "koreader"),
    ):
        row = ub.Annotation(
            user_id=user.id,
            annotation_id=annotation_id,
            book_id=7,
            source=source,
            hidden=False,
        )
        row.sync_targets.append(ub.AnnotationSyncTarget(
            target="stub",
            target_record_id=f"{source}-remote",
            status="synced",
        ))
        s.add(row)
    s.commit()
    handler.calls.clear()
    caplog.set_level("WARNING", logger="cps.services.annotation_sync")

    dispatch_annotation_deletes(
        ["kobo-owned", "webreader-foreign", "koreader-foreign"],
        user,
        book_id=7,
        deletable_sources={"kobo"},
    )

    rows = {
        row.annotation_id: bool(row.hidden)
        for row in s.query(ub.Annotation).order_by(ub.Annotation.id)
    }
    assert rows == {
        "kobo-owned": True,
        "webreader-foreign": False,
        "koreader-foreign": False,
    }
    assert handler.calls == [("delete", "r1")]
    assert "annotation_id='webreader-foreign' stored_source='webreader'" in caplog.text
    assert "annotation_id='koreader-foreign' stored_source='koreader'" in caplog.text


def test_dispatch_delete_skips_tombstoned(patched_session):
    s, user = patched_session
    h = StubHandler()
    register_handler(h)
    dispatch_annotation_sync([_payload("uuid-x")], _book(), user)
    dispatch_annotation_deletes(["uuid-x"], user, deletable_sources={"kobo"})
    h.calls.clear()
    dispatch_annotation_deletes(
        ["uuid-x"], user, deletable_sources={"kobo"},
    )  # second delete attempt
    assert h.calls == []  # handler.delete NOT called twice


def test_malformed_delete_member_cannot_block_a_later_valid_delete(patched_session):
    """One unaddressable member must not turn the rest of the delta into a no-op."""
    s, user = patched_session
    dispatch_annotation_sync([_payload("uuid-keep"), _payload("uuid-delete")], _book(), user)

    dispatch_annotation_deletes(
        [{"not": "an id"}, "uuid-delete"], user, book_id=7,
        deletable_sources={"kobo"},
    )

    rows = {
        row.annotation_id: row.hidden
        for row in s.query(ub.Annotation).order_by(ub.Annotation.id).all()
    }
    assert rows == {"uuid-keep": False, "uuid-delete": True}


def test_tombstone_is_terminal_against_repeat_push(patched_session):
    s, user = patched_session
    register_handler(StubHandler())
    payload = _payload("uuid-x")
    dispatch_annotation_sync([payload], _book(), user)
    dispatch_annotation_deletes(["uuid-x"], user, deletable_sources={"kobo"})
    dispatch_annotation_sync([payload], _book(), user)  # re-push
    st = s.query(ub.AnnotationSyncTarget).one()
    assert st.status == "tombstone"  # NOT resurrected


@pytest.mark.parametrize("bad_location", ["not-an-object", [], {"span": "not-an-object"}])
def test_malformed_location_shape_degrades_without_dropping_the_batch(
    patched_session, bad_location,
):
    """A derived location is optional; its bad shape cannot eat user text or
    prevent a later well-formed annotation in the same PATCH from landing."""
    s, user = patched_session
    malformed = _payload("uuid-bad-location", text_="keep this text")
    malformed["location"] = bad_location

    dispatch_annotation_sync([
        _payload("uuid-before", text_="before"),
        malformed,
        _payload("uuid-after", text_="after"),
    ], _book(), user)

    rows = {
        row.annotation_id: row.highlighted_text
        for row in s.query(ub.Annotation).order_by(ub.Annotation.id).all()
    }
    assert rows == {
        "uuid-before": "before",
        "uuid-bad-location": "keep this text",
        "uuid-after": "after",
    }


def test_non_object_annotation_member_is_skipped_without_dropping_later_members(
    patched_session,
):
    s, user = patched_session

    dispatch_annotation_sync([
        _payload("uuid-before", text_="before"),
        "not-an-annotation-object",
        _payload("uuid-after", text_="after"),
    ], _book(), user)

    assert [row.annotation_id for row in s.query(ub.Annotation).order_by(ub.Annotation.id)] == [
        "uuid-before", "uuid-after",
    ]


def test_database_failure_rolls_back_only_that_member_and_later_members_continue(
    patched_session, monkeypatch,
):
    """Successful members are committed independently; a genuine DB failure is
    explicitly rolled back and cannot poison the scoped session or the rest of
    the device's batch."""
    from cps.services import annotation_sync

    s, user = patched_session
    original = annotation_sync._upsert_annotation
    rollback_calls = []
    original_rollback = s.rollback

    def recording_rollback():
        rollback_calls.append(True)
        return original_rollback()

    def fail_one(session, payload, book, user, **kwargs):
        if payload.get("id") == "uuid-db-failure":
            raise OperationalError("INSERT", {}, RuntimeError("disk unavailable"))
        return original(session, payload, book, user, **kwargs)

    monkeypatch.setattr(s, "rollback", recording_rollback)
    monkeypatch.setattr(annotation_sync, "_upsert_annotation", fail_one)

    result = dispatch_annotation_sync([
        _payload("uuid-before", text_="before"),
        _payload("uuid-db-failure", text_="lost only with failed write"),
        _payload("uuid-after", text_="after"),
    ], _book(), user)

    assert result is False
    assert rollback_calls == [True]
    assert [row.annotation_id for row in s.query(ub.Annotation).order_by(ub.Annotation.id)] == [
        "uuid-before", "uuid-after",
    ]


def test_sync_target_insert_race_does_not_discard_the_annotation(
    patched_session, monkeypatch,
):
    """A losing race on the (annotation_id, target) INSERT must not take the
    user's annotation with it.

    ``_upsert_sync_target`` runs BEFORE ``ub.session_commit()`` and in the same
    transaction as the annotation ``_upsert_annotation`` has flushed but not
    committed. Recovering from the concurrent INSERT with a bare
    ``session.rollback()`` therefore discards the annotation as well — and the
    dispatcher then commits, reports no failure, and the device is told its
    upload succeeded. The content is gone with nothing to recover it from,
    because the device only ever uploads a delta.

    Same precondition ``cps/kobo.py`` already documents for #1318: a SAVEPOINT
    contains only what is flushed after it, so isolating just the target INSERT
    leaves the annotation intact.
    """
    s, user = patched_session
    register_handler(StubHandler())
    original_flush = s.flush
    raced = {"done": False}

    def flush_losing_the_target_race(*args, **kwargs):
        if not raced["done"] and any(
            isinstance(obj, ub.AnnotationSyncTarget) for obj in s.new
        ):
            raced["done"] = True
            raise IntegrityError(
                "INSERT INTO annotation_sync_target", {},
                RuntimeError("UNIQUE constraint failed"),
            )
        return original_flush(*args, **kwargs)

    monkeypatch.setattr(s, "flush", flush_losing_the_target_race)

    dispatch_annotation_sync(
        [_payload("uuid-race", text_="must survive the target race")], _book(), user,
    )

    assert raced["done"], "the test did not actually exercise the INSERT race"
    rows = s.query(ub.Annotation).all()
    assert [r.annotation_id for r in rows] == ["uuid-race"], (
        "the annotation was discarded by the sync-target race recovery"
    )
    assert rows[0].highlighted_text == "must survive the target race"


def test_sync_target_insert_race_still_applies_the_result_to_the_winning_row(
    patched_session, monkeypatch,
):
    """Positive control for the savepoint. Isolating the INSERT must not cost
    the recovery: when a genuine competing row exists, the loser still has to
    find it and apply its result. Without this, a change that simply stopped
    inserting would satisfy the loss test above and quietly break the race
    handling it exists to preserve.
    """
    s, user = patched_session
    register_handler(StubHandler())

    # Give the competing INSERT a valid annotation to reference, then clear the
    # target row so the next dispatch takes the create path again.
    dispatch_annotation_sync([_payload("uuid-a", text_="v1")], _book(), user)
    annotation_id = s.query(ub.Annotation).one().id
    s.query(ub.AnnotationSyncTarget).delete()
    s.commit()

    original_begin_nested = s.begin_nested
    injected = {"done": False}

    def begin_nested_after_a_competitor_commits(*args, **kwargs):
        # Land the competing row in the OUTER transaction, i.e. before the
        # savepoint opens — which is where another session's committed INSERT
        # would already be by the time we lose to it.
        if not injected["done"]:
            injected["done"] = True
            now = datetime.now(timezone.utc)
            s.execute(
                text(
                    "INSERT INTO annotation_sync_target "
                    "(annotation_id, target, status, created_at, updated_at) "
                    "VALUES (:a, 'stub', 'pending', :t, :t)"
                ),
                {"a": annotation_id, "t": now},
            )
        return original_begin_nested(*args, **kwargs)

    monkeypatch.setattr(s, "begin_nested", begin_nested_after_a_competitor_commits)

    dispatch_annotation_sync([_payload("uuid-a", text_="v2")], _book(), user)

    assert injected["done"], "the test did not actually exercise the INSERT race"
    targets = s.query(ub.AnnotationSyncTarget).all()
    assert len(targets) == 1, "the race must converge on exactly one target row"
    assert targets[0].status == "synced", (
        "the loser must apply its result to the winning row"
    )
    assert s.query(ub.Annotation).one().highlighted_text == "v2"
