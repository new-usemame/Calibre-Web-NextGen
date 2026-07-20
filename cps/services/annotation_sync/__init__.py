# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Annotation sync target dispatcher.

Public API:
  - register_handler(handler): plug in a new target
  - available_targets(): list registered target names
  - dispatch_annotation_sync(payload_annotations, book, user): push every annotation
  - dispatch_annotation_deletes(deleted_ids, user, book_id): delete scoped annotations

The dispatcher owns all DB persistence — Annotation rows + AnnotationSyncTarget
rows + the status state machine. Handlers are stateless: they make remote
calls and return SyncResult.

See notes/2026-05-21-annotation-decouple-source-target-DESIGN.md §3.4.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.exc import IntegrityError

from .base import AnnotationSyncTargetHandler, SyncResult

log = logging.getLogger(__name__)

_HANDLERS: Dict[str, AnnotationSyncTargetHandler] = {}

# Background dispatch seam (#920)
# ------------------------------
# Handlers reach third-party APIs over blocking sockets. CWNG serves requests
# with gevent and deliberately does NOT monkey-patch (see
# cps/services/parallel.py), so a blocking socket on a request greenlet stops
# the WHOLE application, not just that request — measured at ~10s of frozen app
# per annotation, which also tripped the Docker healthcheck into restarting the
# container and blew past the KOReader plugin's 15s timeout (#920/#699).
#
# So the remote half of the fan-out belongs on the WorkerThread, exactly like
# the shelf-add sync already does (cps/tasks/hardcover_sync.py). The request
# path persists locally, marks each target ``pending`` and returns; the worker
# performs the push/delete on its own thread with its own session.
#
# The seam stays OFF by default so unit tests (and any embedding that has no
# worker) keep the synchronous behaviour. cps.main turns it on at startup.
_REMOTE_ENQUEUE = None


def set_remote_enqueue(fn) -> None:
    """Install (or clear, with ``None``) the background enqueue hook.

    ``fn(user, jobs)`` receives the ub.User and a list of job dicts, either
    ``{"op": "push", "annotation": <id>, "book": <id>, "payload": {...}|None}``
    or ``{"op": "delete", "sync_target": <id>}``.
    """
    global _REMOTE_ENQUEUE
    _REMOTE_ENQUEUE = fn


def enable_background_dispatch() -> None:
    """Wire the WorkerThread-backed enqueue. Called once at app startup."""
    from cps.tasks.annotation_sync import enqueue_annotation_sync
    set_remote_enqueue(enqueue_annotation_sync)


def _background_enqueue():
    return _REMOTE_ENQUEUE


def _enqueue(user, jobs, book=None) -> None:
    """Hand queued jobs to the background worker.

    A failure to enqueue must not lose the sync, so we fall back to running the
    fan-out inline — slow, but the annotation still reaches the remote. The
    local rows are already committed by the time we get here either way.
    """
    if not jobs:
        return
    fn = _background_enqueue()
    if fn is None:
        return
    try:
        fn(user, jobs)
    except Exception:
        log.exception("annotation_sync: enqueue failed; running fan-out inline")
        run_jobs_inline(user, jobs, book=book)


def run_jobs_inline(user, jobs, book=None) -> None:
    """Execute queued jobs against the request-thread session (fallback path).

    The caller is still holding the book it just dispatched for, so pass it
    through rather than re-reading it out of the Calibre DB.
    """
    from cps import ub
    loader = None if book is None else (lambda _book_id: book)
    execute_jobs(ub.session, user, jobs, book_loader=loader)
    ub.session_commit()


def register_handler(handler: AnnotationSyncTargetHandler) -> None:
    """Register a handler. Replaces any previous handler with the same target_name."""
    _HANDLERS[handler.target_name] = handler


def available_targets() -> List[str]:
    return list(_HANDLERS.keys())


def _registered_handlers():
    return list(_HANDLERS.values())


def reset_registry_for_testing() -> None:
    """Test-only: clear registered handlers between tests."""
    _HANDLERS.clear()


def _now():
    return datetime.now(timezone.utc)


def _book_uuid(book):
    """Best-effort UUID extraction for the book (used to build content_id)."""
    uuid_attr = getattr(book, "uuid", None)
    if uuid_attr:
        return uuid_attr
    return None


def _upsert_annotation(session, payload, book, user):
    """Find-or-create Annotation row keyed on (user_id, book_id, annotation_id).

    Populates content fields AND position fields from the Kobo PATCH payload
    so subsequent CFI computation has everything it needs.  This is the
    sub-project (2) work: annotation persistence happens unconditionally —
    independent of any registered sync target (Hardcover etc.).
    """
    from cps import ub
    annotation_id = payload.get("id")
    if not annotation_id:
        return None
    ann = (
        session.query(ub.Annotation)
        .filter(
            ub.Annotation.user_id == user.id,
            ub.Annotation.book_id == book.id,
            ub.Annotation.annotation_id == annotation_id,
        )
        .first()
    )
    if ann is None:
        ann = ub.Annotation(
            user_id=user.id,
            annotation_id=annotation_id,
            book_id=book.id,
            source="kobo",
        )
        session.add(ann)
    # If a previously soft-deleted (hidden) annotation comes back, un-hide it.
    ann.hidden = False
    # Content fields
    if "highlightedText" in payload:
        ann.highlighted_text = payload.get("highlightedText")
    if "noteText" in payload:
        ann.note_text = payload.get("noteText")
    if "highlightColor" in payload:
        ann.highlight_color = payload.get("highlightColor")
    # Position fields — pulled from Kobo's location.span block.
    span = (payload.get("location") or {}).get("span") or {}
    chapter_progress = span.get("chapterProgress")
    if chapter_progress is not None:
        ann.chapter_progress = chapter_progress
    chapter_filename = span.get("chapterFilename")
    if chapter_filename:
        uuid = _book_uuid(book)
        if uuid:
            ann.content_id = f"{uuid}!!{chapter_filename}"
    if "startPath" in span:
        ann.start_container_path = span.get("startPath")
    if "endPath" in span:
        ann.end_container_path = span.get("endPath")
    if "startChar" in span:
        ann.start_offset = span.get("startChar")
    if "endChar" in span:
        ann.end_offset = span.get("endChar")
    if "contextString" in span or "context" in span:
        ann.context_string = span.get("contextString") or span.get("context")
    ann.last_synced = _now()
    session.flush()
    return ann


def _apply_result(st, result):
    """Mutate AnnotationSyncTarget in place from a SyncResult + log transition."""
    prior = st.status
    st.status = result.status
    if result.target_record_id:
        st.target_record_id = result.target_record_id
    if result.status == "synced":
        st.last_synced = _now()
        st.error_message = None
    else:
        st.error_message = result.error_message
    st.last_attempt = _now()
    st.updated_at = _now()
    log.info(
        "annotation_sync transition: annotation_id=%s target=%s %s->%s err=%r",
        st.annotation_id, st.target, prior, result.status, result.error_message,
    )


def _upsert_sync_target(session, annotation, target_name, result):
    """Find-or-create the (annotation_id, target) row, race-safe under
    concurrent INSERT via IntegrityError recovery."""
    from cps import ub
    st = (
        session.query(ub.AnnotationSyncTarget)
        .filter(
            ub.AnnotationSyncTarget.annotation_id == annotation.id,
            ub.AnnotationSyncTarget.target == target_name,
        )
        .first()
    )
    if st is None:
        st = ub.AnnotationSyncTarget(
            annotation_id=annotation.id,
            target=target_name,
            status=result.status,
            target_record_id=result.target_record_id,
            error_message=result.error_message,
            last_attempt=_now(),
            last_synced=_now() if result.status == "synced" else None,
            created_at=_now(),
            updated_at=_now(),
        )
        session.add(st)
        try:
            session.flush()
        except IntegrityError:
            # Concurrent INSERT — recover by re-reading + applying result.
            session.rollback()
            st = (
                session.query(ub.AnnotationSyncTarget)
                .filter(
                    ub.AnnotationSyncTarget.annotation_id == annotation.id,
                    ub.AnnotationSyncTarget.target == target_name,
                )
                .first()
            )
            if st is not None:
                _apply_result(st, result)
        else:
            # Log new-row creation for parity with _apply_result on update.
            log.info(
                "annotation_sync transition: annotation_id=%s target=%s NEW->%s err=%r",
                annotation.id, target_name, result.status, result.error_message,
            )
        return st
    _apply_result(st, result)
    return st


def push_annotation_to_handlers(session, annotation, book, user, payload=None,
                                handlers=None) -> None:
    """Run the remote push for one annotation against every enabled handler and
    persist the outcome on its AnnotationSyncTarget row.

    Split out of ``dispatch_annotation_sync`` so the background worker can run
    exactly the same fan-out against its own thread-local session (#920).
    """
    for handler in (handlers if handlers is not None else _registered_handlers()):
        if not handler.is_enabled(user):
            continue
        handler = handler.for_session(session)
        existing = annotation.sync_target(handler.target_name)
        if existing is not None and existing.status == "tombstone":
            # Terminal — never re-push a tombstoned annotation.
            continue
        try:
            result = handler.push(annotation, book, user, payload=payload)
        except Exception as exc:
            log.exception("dispatcher: handler %s push raised", handler.target_name)
            result = SyncResult(status="failed", error_message=str(exc))
        _upsert_sync_target(session, annotation, handler.target_name, result)


def delete_sync_target(session, sync_target, user) -> None:
    """Run the remote delete for one AnnotationSyncTarget row and persist the
    outcome. Counterpart of :func:`push_annotation_to_handlers` (#920)."""
    if sync_target.status == "tombstone":
        return
    handler = _HANDLERS.get(sync_target.target)
    if handler is None or not handler.is_enabled(user):
        return
    handler = handler.for_session(session)
    try:
        result = handler.delete(sync_target, user)
    except Exception as exc:
        log.exception("dispatcher: handler %s delete raised", handler.target_name)
        result = SyncResult(status="failed", error_message=str(exc))
    _apply_result(sync_target, result)


def _default_book_loader(book_id):
    from cps import calibre_db, db
    return (
        calibre_db.session.query(db.Books)
        .filter(db.Books.id == book_id)
        .first()
    )


def execute_jobs(session, user, jobs, book_loader=None) -> None:
    """Run queued push/delete jobs against ``session``.

    Shared by the background task and the inline fallback so both paths go
    through identical handler semantics. One failing job never strands the
    rest of the batch — the annotation is already persisted locally, and its
    target row keeps the error.
    """
    from cps import ub
    if book_loader is None:
        book_loader = _default_book_loader
    books = {}
    for job in jobs or []:
        op = job.get("op")
        try:
            if op == "push":
                ann = (
                    session.query(ub.Annotation)
                    .filter(ub.Annotation.id == job.get("annotation"))
                    .first()
                )
                if ann is None:
                    continue
                book_id = job.get("book")
                if book_id not in books:
                    books[book_id] = book_loader(book_id)
                book = books[book_id]
                if book is None:
                    log.warning("annotation_sync: book %s gone; skipping push", book_id)
                    continue
                push_annotation_to_handlers(
                    session, ann, book, user, payload=job.get("payload"),
                )
            elif op == "delete":
                st = (
                    session.query(ub.AnnotationSyncTarget)
                    .filter(ub.AnnotationSyncTarget.id == job.get("sync_target"))
                    .first()
                )
                if st is None:
                    continue
                delete_sync_target(session, st, user)
            else:
                log.warning("annotation_sync: unknown job op %r", op)
        except Exception:
            log.exception("annotation_sync: job %r failed", job)


def _mark_pending(session, annotation, user):
    """Put every enabled, non-terminal target for this annotation into
    ``pending`` so the row reflects "queued, not yet pushed" while the worker
    catches up. Returns True when at least one target is actually queued.

    Disabled handlers are skipped here exactly as they are in the fan-out — a
    target nobody is going to push to must not leave a ``pending`` row behind.
    """
    queued = False
    for handler in _registered_handlers():
        if not handler.is_enabled(user):
            continue
        existing = annotation.sync_target(handler.target_name)
        if existing is not None and existing.status == "tombstone":
            continue
        _upsert_sync_target(
            session, annotation, handler.target_name,
            SyncResult(status="pending"),
        )
        queued = True
    return queued


def dispatch_annotation_sync(payload_annotations, book, user) -> None:
    """For each annotation in the PATCH payload, persist locally then push to each enabled handler."""
    from cps import ub
    if not payload_annotations:
        return
    jobs = []
    for payload in payload_annotations:
        ann = _upsert_annotation(ub.session, payload, book, user)
        if ann is None:
            continue
        if _background_enqueue() is not None:
            if _mark_pending(ub.session, ann, user):
                jobs.append({"op": "push", "annotation": ann.id,
                             "book": book.id, "payload": payload})
            continue
        push_annotation_to_handlers(ub.session, ann, book, user, payload=payload)
    ub.session_commit()
    _enqueue(user, jobs, book=book)


def dispatch_existing_annotation_sync(annotation, book, user) -> None:
    """Push an already-persisted Annotation row to each enabled sync target.

    The Kobo PATCH path (``dispatch_annotation_sync``) upserts the row from a
    payload first; web-reader-created (and other non-PATCH origin) rows already
    exist, so this is their fan-out entry point. Same per-handler semantics:
    skip disabled handlers, never re-push a tombstoned target, record the
    result on the AnnotationSyncTarget row.
    """
    from cps import ub
    if annotation is None:
        return
    jobs = []
    if _background_enqueue() is not None:
        if _mark_pending(ub.session, annotation, user):
            jobs.append({"op": "push", "annotation": annotation.id, "book": book.id})
    else:
        push_annotation_to_handlers(ub.session, annotation, book, user)
    ub.session_commit()
    _enqueue(user, jobs, book=book)


def dispatch_annotation_deletes(deleted_ids, user, book_id=None) -> None:
    """For each annotation_id, transition non-tombstone sync_targets via
    handler.delete AND soft-delete the local Annotation row by setting
    ``hidden=True``.

    Sub-project (2): local soft-delete happens unconditionally — independent
    of any enabled sync target. Recovery is symmetric: a subsequent
    create/update PATCH for the same annotation_id un-hides it via
    ``_upsert_annotation``.
    """
    from cps import ub
    if not deleted_ids:
        return
    jobs = []
    for annotation_id in deleted_ids:
        query = ub.session.query(ub.Annotation).filter(
            ub.Annotation.user_id == user.id,
            ub.Annotation.annotation_id == annotation_id,
        )
        if book_id is not None:
            query = query.filter(ub.Annotation.book_id == book_id)
        ann = query.first()
        if ann is None:
            continue
        # Push delete through any non-tombstone sync targets.
        for st in list(ann.sync_targets):
            if st.status == "tombstone":
                continue
            handler = _HANDLERS.get(st.target)
            if handler is None or not handler.is_enabled(user):
                continue
            if _background_enqueue() is not None:
                jobs.append({"op": "delete", "sync_target": st.id})
                continue
            delete_sync_target(ub.session, st, user)
        # Soft-delete the local row regardless of sync target outcome.
        ann.hidden = True
        log.info(
            "annotation_sync: soft-delete annotation_id=%s (hidden=True)",
            annotation_id,
        )
    ub.session_commit()
    _enqueue(user, jobs)


# Auto-register Hardcover at import time.
from .hardcover import HardcoverHandler  # noqa: E402
register_handler(HardcoverHandler())
