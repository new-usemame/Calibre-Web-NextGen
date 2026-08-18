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

from ..annotation_colors import to_display_name, to_storage_color
from .base import AnnotationSyncTargetHandler, SyncResult

log = logging.getLogger(__name__)

_HANDLERS: Dict[str, AnnotationSyncTargetHandler] = {}
_CLIENT_TIME_MISSING = object()


def parse_client_modified_utc(value):
    """Parse a client clock to naive UTC; distinguish missing from invalid."""
    if value is _CLIENT_TIME_MISSING:
        return _CLIENT_TIME_MISSING
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)

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


def _kobo_payload_matches_row(annotation, payload, span, normalized_content_id):
    """True only when applying the payload would leave every stored field unchanged."""
    def supplied(mapping, key, current):
        return mapping.get(key) if key in mapping else current

    chapter_progress = span.get("chapterProgress")
    next_context = annotation.context_string
    if "contextString" in span or "context" in span:
        next_context = span.get("contextString") or span.get("context")
    current = (
        annotation.highlighted_text, annotation.note_text,
        to_storage_color(annotation.highlight_color),
        annotation.chapter_progress, annotation.content_id,
        annotation.start_container_path, annotation.end_container_path,
        annotation.start_offset, annotation.end_offset,
        annotation.context_string, bool(annotation.hidden),
    )
    incoming = (
        supplied(payload, "highlightedText", annotation.highlighted_text),
        supplied(payload, "noteText", annotation.note_text),
        # Both sides through the same normaliser: the device's hex and a
        # legacy row's colour NAME are the same colour, and a PATCH that
        # changes nothing must not be counted as a change just because the
        # stored spelling is older than the wire one.
        to_storage_color(supplied(payload, "highlightColor", annotation.highlight_color)),
        chapter_progress if chapter_progress is not None else annotation.chapter_progress,
        normalized_content_id or annotation.content_id,
        supplied(span, "startPath", annotation.start_container_path),
        supplied(span, "endPath", annotation.end_container_path),
        supplied(span, "startChar", annotation.start_offset),
        supplied(span, "endChar", annotation.end_offset),
        next_context, False,
    )
    return incoming == current


def _upsert_annotation(session, payload, book, user, *, origin_device_id=None):
    """Find-or-create Annotation row keyed on (user_id, book_id, annotation_id).

    Populates content fields AND position fields from the Kobo PATCH payload
    so subsequent CFI computation has everything it needs.  This is the
    sub-project (2) work: annotation persistence happens unconditionally —
    independent of any registered sync target (Hardcover etc.).
    """
    from cps import ub
    if not isinstance(payload, dict):
        log.warning("Skipping non-object annotation payload of type %s", type(payload).__name__)
        return None
    annotation_id = payload.get("id")
    if not annotation_id:
        return None
    raw_client_time = payload.get("clientLastModifiedUtc", _CLIENT_TIME_MISSING)
    client_time = parse_client_modified_utc(raw_client_time)
    client_time_rejected = False
    if raw_client_time is not _CLIENT_TIME_MISSING and client_time is None:
        # A rejected reading is NOT an absent reading. Existing rows deliberately
        # ignore undated updates, but applying that policy here would discard the
        # user's edit over a malformed ordering hint. Apply by arrival order and
        # retain any last-known-valid stored clock.
        log.warning(
            "Annotation %s has a malformed clientLastModifiedUtc %r; storing it "
            "without a client timestamp rather than discarding the highlight",
            annotation_id, raw_client_time,
        )
        client_time = _CLIENT_TIME_MISSING
        client_time_rejected = True

    location = payload.get("location")
    if location is None:
        span = {}
    elif not isinstance(location, dict):
        log.warning(
            "Annotation %s has a non-object location; preserving the annotation "
            "without the rejected derived location", annotation_id,
        )
        span = {}
    else:
        supplied_span = location.get("span")
        if supplied_span is None:
            span = {}
        elif not isinstance(supplied_span, dict):
            log.warning(
                "Annotation %s has a non-object location.span; preserving the "
                "annotation without the rejected derived location", annotation_id,
            )
            span = {}
        else:
            span = supplied_span
    normalized_content_id = None
    chapter_filename = span.get("chapterFilename")
    if chapter_filename and _book_uuid(book):
        from cps.services.annotation_content_id import normalize_content_id, ContentIdError
        try:
            normalized_content_id = normalize_content_id(
                f"{_book_uuid(book)}!!{chapter_filename}", book_uuid=_book_uuid(book)
            )
        except ContentIdError:
            # content_id is derived and nullable; the highlight is neither. A
            # rejected replacement also does not prove an existing validated
            # locator is wrong, so preserve the last-known-valid value. The
            # content-id backfill only normalizes non-NULL values and cannot
            # reconstruct one after it has been cleared.
            log.warning(
                "Annotation %s has an unusable content location %r; storing it "
                "without discarding the highlight or its last valid content_id",
                annotation_id, chapter_filename,
            )
            normalized_content_id = None
    ann = (
        session.query(ub.Annotation)
        .filter(
            ub.Annotation.user_id == user.id,
            ub.Annotation.book_id == book.id,
            ub.Annotation.annotation_id == annotation_id,
        )
        .first()
    )
    if ann is not None and ann.client_modified_at is not None:
        stored = ann.client_modified_at
        if stored.tzinfo is not None:
            stored = stored.astimezone(timezone.utc).replace(tzinfo=None)
        if client_time is _CLIENT_TIME_MISSING and not client_time_rejected:
            log.info("Ignoring stale or undated update for annotation %s", annotation_id)
            return None
        if client_time is not _CLIENT_TIME_MISSING and client_time < stored:
            log.info("Ignoring stale or undated update for annotation %s", annotation_id)
            return None
        if client_time is not _CLIENT_TIME_MISSING and client_time == stored:
            # Kobo clocks have second precision. A byte-equivalent retry is a
            # no-op, but a real edit can share the same second and must not be
            # eaten merely because its clock ties. Equal-clock divergent
            # payloads therefore use arrival order as the deterministic tie.
            if _kobo_payload_matches_row(ann, payload, span, normalized_content_id):
                return None
    created = ann is None
    if created:
        ann = ub.Annotation(
            user_id=user.id,
            annotation_id=annotation_id,
            book_id=book.id,
            source="kobo",
            origin_device_id=origin_device_id,
        )
        session.add(ann)
    elif getattr(ann, "content_revision", None) is None:
        ann.content_revision = 1
    else:
        ann.content_revision += 1
    # If a previously soft-deleted (hidden) annotation comes back, un-hide it.
    ann.hidden = False
    # Content fields
    if "highlightedText" in payload:
        ann.highlighted_text = payload.get("highlightedText")
    if "noteText" in payload:
        ann.note_text = payload.get("noteText")
    if "highlightColor" in payload:
        # The device already sends the canonical wire hex; normalising is a
        # no-op for it and repairs a legacy name from any other client.
        ann.highlight_color = to_storage_color(payload.get("highlightColor"))
    if "type" in payload:
        native_type = payload.get("type")
        if isinstance(native_type, str) and len(native_type) <= 32:
            ann.annotation_type = native_type
    # Position fields — pulled from Kobo's location.span block.
    chapter_progress = span.get("chapterProgress")
    if chapter_progress is not None:
        ann.chapter_progress = chapter_progress
    if normalized_content_id:
        ann.content_id = normalized_content_id
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
    if client_time is not _CLIENT_TIME_MISSING:
        ann.client_modified_at = client_time
    ann.server_modified_at = _now()
    ann.last_editor_device_id = origin_device_id
    ann.last_synced = _now()
    session.flush()
    return ann


def _store_raw_materialization(session, annotation, raw_record, *, trace_id=None):
    """Best-effort sidecar upsert isolated behind a SQLite savepoint."""
    from cps import ub
    from cps.services import kobo_annotation_stage0

    if raw_record is None or raw_record.annotation_id != annotation.annotation_id:
        return None
    try:
        with session.begin_nested():
            row = (
                session.query(ub.KoboAnnotationMaterialization)
                .filter(ub.KoboAnnotationMaterialization.annotation_id == annotation.id)
                .first()
            )
            now = _now()
            if row is None:
                row = ub.KoboAnnotationMaterialization(
                    annotation_id=annotation.id,
                    materialization_revision=1,
                    provenance="kobo_patch",
                    serveable=False,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.materialization_revision += 1
                row.updated_at = now
            row.raw_annotation_json = raw_record.raw_annotation_json
            row.raw_location_json = raw_record.raw_location_json
            row.raw_client_modified_utc = raw_record.raw_client_modified_utc
            row.payload_sha256 = raw_record.payload_sha256
            row.attachments_state = raw_record.attachments_state
            row.provenance = "kobo_patch"
            # PATCH is a delta and never establishes serveability in Stage 0.
            row.serveable = False
            row.quarantine_reason = None
            session.flush()
        return True
    except Exception:
        log.exception(
            "Kobo raw lexical capture failed trace_id=%s user_id=%s book_id=%s",
            trace_id, annotation.user_id, annotation.book_id,
        )
        kobo_annotation_stage0.record_event(
            "raw_capture", "failed", trace_id=trace_id,
            user_id=annotation.user_id, book_id=annotation.book_id,
            annotation_count=1,
        )
        return False


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
    concurrent INSERT via IntegrityError recovery.

    The INSERT is isolated behind a SAVEPOINT, and that is load-bearing rather
    than tidiness. This runs BEFORE ``ub.session_commit()`` and in the same
    transaction as the annotation ``_upsert_annotation`` has just flushed but
    not committed, so recovering from the race with a bare
    ``session.rollback()`` discarded the user's annotation along with the
    losing target row — after which the caller committed, logged no failure,
    and the device was told its upload succeeded. Since the device only ever
    uploads a delta, that content had nothing left to recover it from.

    A SAVEPOINT contains only what is flushed after it, which is exactly the
    precondition ``cps/kobo.py`` documents for #1318: the annotation is already
    flushed, so it survives the savepoint being rolled back.
    """
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
        try:
            with session.begin_nested():
                session.add(st)
                session.flush()
        except IntegrityError:
            # Concurrent INSERT — the savepoint (and only the savepoint) is
            # rolled back, so the flushed annotation is still staged. Recover
            # by re-reading + applying result.
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


def dispatch_annotation_sync(payload_annotations, book, user, *, origin_device_id=None,
                             raw_materializations=None, trace_id=None) -> None:
    """Persist each valid annotation independently, then fan it out.

    Device batches are a transport convenience, not a transaction boundary: one
    malformed member or failed write must not roll back already-preserved user
    data or prevent later members from being attempted.
    """
    from cps import ub
    if not payload_annotations:
        return
    if not isinstance(payload_annotations, list):
        log.warning("Skipping annotation batch because updatedAnnotations is not a list")
        return
    jobs = []
    for index, payload in enumerate(payload_annotations):
        if not isinstance(payload, dict):
            log.warning(
                "Skipping non-object annotation member at updatedAnnotations[%d]", index,
            )
            continue
        pending_job = None
        try:
            ann = _upsert_annotation(
                ub.session, payload, book, user, origin_device_id=origin_device_id,
            )
            if ann is None:
                continue
            raw_record = None
            if raw_materializations is not None and index < len(raw_materializations):
                raw_record = raw_materializations[index]
            raw_capture_staged = _store_raw_materialization(
                ub.session, ann, raw_record, trace_id=trace_id,
            )
            if _background_enqueue() is not None:
                if _mark_pending(ub.session, ann, user):
                    pending_job = {"op": "push", "annotation": ann.id,
                                   "book": book.id, "payload": payload}
            else:
                push_annotation_to_handlers(ub.session, ann, book, user, payload=payload)
            committed = ub.session_commit()
            if committed is False:
                log.error(
                    "Annotation %s could not be committed; continuing with the batch",
                    payload.get("id"),
                )
                continue
            if raw_capture_staged:
                from cps.services import kobo_annotation_stage0
                kobo_annotation_stage0.record_event(
                    "raw_capture", "stored", trace_id=trace_id,
                    user_id=ann.user_id, book_id=ann.book_id,
                    annotation_count=1,
                )
        except Exception:
            # A failed SQLAlchemy transaction poisons the scoped session until an
            # explicit rollback. Do that here, then continue: prior annotations
            # were committed independently and later annotations still deserve a
            # chance to persist.
            log.exception(
                "Annotation member updatedAnnotations[%d] failed; rolling back that "
                "member and continuing", index,
            )
            ub.session.rollback()
            continue
        if pending_job is not None:
            jobs.append(pending_job)
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
        ann.content_revision = (ann.content_revision or 1) + 1
        ann.server_modified_at = _now()
        log.info(
            "annotation_sync: soft-delete annotation_id=%s (hidden=True)",
            annotation_id,
        )
    ub.session_commit()
    _enqueue(user, jobs)


# Auto-register Hardcover at import time.
from .hardcover import HardcoverHandler  # noqa: E402
register_handler(HardcoverHandler())
