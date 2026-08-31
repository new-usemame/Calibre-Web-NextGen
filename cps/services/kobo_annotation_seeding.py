# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Durably seed CWNG's owned-book Kobo annotation authority.

The device always receives Kobo's proxied response for the seeding request.
This module records that response, reconciles the complete upstream set into
the generic annotation store, and promotes only after the replacement set is
provably complete and small enough for CWNG's single-page local renderer.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from cps import ub
from cps.services import kobo_annotation_stage0


LOCAL_PAGE_CAPACITY = 100
PENDING_CAPTURE_TTL = timedelta(minutes=15)
_SAFE_FAILURE_REASONS = frozenset({
    "seed_authority_revision_changed",
    "seed_capture_expired",
    "seed_capture_requires_pagination",
    "seed_capture_superseded",
    "seed_content_id_conflict",
    "seed_duplicate_annotation_id",
    "seed_local_count_below_capture",
    "seed_local_set_missing_captured_id",
    "seed_local_set_requires_pagination",
    "seed_response_invalid",
    "seed_row_conflict_local_won",
    "seed_row_conflict_unresolved",
})


def _now():
    return datetime.now(timezone.utc)


def _normalized_book_uuid(book):
    value = getattr(book, "uuid", None)
    if not isinstance(value, str):
        return None
    value = value.strip().strip("{}").strip().casefold()
    return value if value and len(value) <= 64 else None


def _state_for_book(user_id, book_id):
    return (
        ub.session.query(ub.KoboAnnotationBookState)
        .filter(
            ub.KoboAnnotationBookState.user_id == user_id,
            ub.KoboAnnotationBookState.book_id == book_id,
        )
        .first()
    )


def _requesting_device(user_id, device_id):
    if not isinstance(device_id, int) or isinstance(device_id, bool):
        return None
    return (
        ub.session.query(ub.Device)
        .filter(
            ub.Device.id == device_id,
            ub.Device.user_id == user_id,
            ub.Device.kind == "kobo",
        )
        .first()
    )


def _accepted_capture(book_state_id, device_id):
    return (
        ub.session.query(ub.KoboAnnotationSeedCapture.id)
        .filter(
            ub.KoboAnnotationSeedCapture.book_state_id == book_state_id,
            ub.KoboAnnotationSeedCapture.device_id == device_id,
            ub.KoboAnnotationSeedCapture.result == "accepted",
            ub.KoboAnnotationSeedCapture.completed_at.isnot(None),
        )
        .first()
    )


def _pending_capture(book_state_id):
    """Return the single SQLite-owned pending reconciliation capture."""
    return (
        ub.session.query(ub.KoboAnnotationSeedCapture)
        .filter(
            ub.KoboAnnotationSeedCapture.book_state_id == book_state_id,
            ub.KoboAnnotationSeedCapture.result == "pending",
        )
        .order_by(
            ub.KoboAnnotationSeedCapture.started_at.desc(),
            ub.KoboAnnotationSeedCapture.id.desc(),
        )
        .first()
    )


def _expected_offset(capture):
    page = (
        ub.session.query(ub.KoboAnnotationSeedCapturePage)
        .filter(ub.KoboAnnotationSeedCapturePage.seed_capture_id == capture.id)
        .order_by(ub.KoboAnnotationSeedCapturePage.page_number.desc())
        .first()
    )
    return None if page is None else page.next_offset_token


def _as_utc(value):
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _capture_expired(capture, *, now=None):
    started_at = _as_utc(getattr(capture, "started_at", None))
    if started_at is None:
        return True
    return (now or _now()) - started_at >= PENDING_CAPTURE_TTL


def _retire_pending(capture, reason):
    """Release one pending-owner slot without globally quarantining a book."""
    state = capture.book_state
    capture.completed_at = _now()
    capture.page_count = len(capture.pages)
    capture.result = "failed"
    capture.failure_reason = (
        reason if reason in _SAFE_FAILURE_REASONS else "seed_response_invalid"
    )
    if state.authority_status == "seeding" and not state.ever_authoritative:
        state.authority_status = "unseeded"
        state.quarantine_reason = None


def _capture_revision_is_current(capture):
    state_revision = getattr(capture.book_state, "authority_revision", None)
    started_revision = getattr(capture, "started_authority_revision", None)
    return (
        isinstance(state_revision, int)
        and not isinstance(state_revision, bool)
        and isinstance(started_revision, int)
        and not isinstance(started_revision, bool)
        and state_revision == started_revision
    )


def _seeding_gates_allow(settings, user):
    try:
        return (
            not kobo_annotation_stage0.emergency_override_disables()
            and kobo_annotation_stage0.schema_capable(ub.session.get_bind())
            and bool(getattr(settings, "config_kobo_two_way_annotation_sync", False))
            and bool(getattr(user, "kobo_two_way_annotation_sync", False))
        )
    except Exception:
        return False


def begin_or_resume_capture(
    *, settings, user, book, device_id, request_offset_token,
    device_etag=None, log,
):
    """Return a pending capture id for this proxied GET, or ``None``.

    A first seed transitions an unseeded book to ``seeding``. Once one device
    has promoted the user-wide set, another active Kobo can capture against the
    same authoritative state without disrupting devices that are already local.
    """
    user_id = getattr(user, "id", None)
    book_id = getattr(book, "id", None)
    if not _seeding_gates_allow(settings, user):
        return None
    if _requesting_device(user_id, device_id) is None:
        return None

    try:
        state = _state_for_book(user_id, book_id)
        if state is None:
            normalized_content_id = _normalized_book_uuid(book)
            if normalized_content_id is None:
                return None
            conflict = (
                ub.session.query(ub.KoboAnnotationBookState.id)
                .filter(
                    ub.KoboAnnotationBookState.user_id == user_id,
                    ub.KoboAnnotationBookState.content_id == normalized_content_id,
                    ub.KoboAnnotationBookState.book_id != book_id,
                )
                .first()
            )
            if conflict is not None:
                return None
            candidate = ub.KoboAnnotationBookState(
                user_id=user_id,
                book_id=book_id,
                content_id=normalized_content_id,
                authority_status="unseeded",
                authority_revision=0,
                generation_id=str(uuid.uuid4()),
                ever_authoritative=False,
                opaque_content_status="unknown",
            )
            try:
                with ub.begin_contained_nested(ub.session):
                    ub.session.add(candidate)
                    ub.session.flush()
            except IntegrityError:
                state = _state_for_book(user_id, book_id)
                if state is None:
                    return None
            else:
                state = candidate

        pending = _pending_capture(state.id)
        if pending is not None:
            expected_offset = _expected_offset(pending)
            same_device = pending.device_id == device_id
            dead_cursor_restart = (
                same_device
                and request_offset_token is None
                and expected_offset is not None
            )
            expired = _capture_expired(pending)
            if expired or dead_cursor_restart:
                _retire_pending(
                    pending,
                    "seed_capture_expired" if expired
                    else "seed_capture_superseded",
                )
                # A continuation token can only belong to the retired owner.
                # Persist the release, but never attach it to a fresh capture.
                if request_offset_token is not None:
                    ub.session_commit()
                    return None
                ub.session.flush()
            elif not same_device or expected_offset != request_offset_token:
                return None
            else:
                return pending.id

        # Never start a new capture in the middle of an upstream page chain.
        if request_offset_token is not None:
            return None

        device_has_seed = _accepted_capture(state.id, device_id) is not None
        first_seed = state.authority_status == "unseeded"
        missing_device_seed = (
            state.authority_status == "authoritative" and not device_has_seed
        )
        if not (first_seed or missing_device_seed):
            return None

        capture = ub.KoboAnnotationSeedCapture(
            book_state_id=state.id,
            device_id=device_id,
            started_at=_now(),
            started_authority_revision=state.authority_revision or 0,
            device_etag=device_etag,
            result="pending",
            seed_kind="upstream_capture",
        )
        ub.session.add(capture)
        if first_seed:
            state.authority_status = "seeding"
            state.quarantine_reason = None
        ub.session.flush()
        capture_id = capture.id
        if ub.session_commit() is False:
            return None
        kobo_annotation_stage0.record_event(
            "seed_capture", "started", user_id=user_id, book_id=book_id,
        )
        return capture_id
    except Exception:
        ub.session.rollback()
        log.exception(
            "Kobo annotation seed capture could not start user_id=%s book_id=%s",
            user_id, book_id,
        )
        return None


def _parse_page(raw_body):
    try:
        payload = json.loads(raw_body)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("annotations"), list):
        return None
    next_offset = payload.get("nextPageOffsetToken")
    if next_offset is not None and (
        not isinstance(next_offset, str) or not next_offset
    ):
        return None
    return payload["annotations"], next_offset


def _load_captured_pages(capture_id):
    pages = (
        ub.session.query(ub.KoboAnnotationSeedCapturePage)
        .filter(ub.KoboAnnotationSeedCapturePage.seed_capture_id == capture_id)
        .order_by(ub.KoboAnnotationSeedCapturePage.page_number.asc())
        .all()
    )
    annotations = []
    raw_pages = []
    for expected_number, page in enumerate(pages):
        if page.page_number != expected_number:
            raise ValueError("non-contiguous capture pages")
        raw_body = gzip.decompress(bytes(page.response_body_gzip))
        if hashlib.sha256(raw_body).hexdigest() != page.response_sha256:
            raise ValueError("capture page digest mismatch")
        parsed = _parse_page(raw_body)
        if parsed is None:
            raise ValueError("invalid captured page")
        page_annotations, _next_offset = parsed
        annotations.extend(page_annotations)
        raw_pages.append(raw_body)
    return pages, annotations, raw_pages


def accepted_identity_requirements(book_state_id, *, device_id=None):
    """Return ``{annotation_id: captured_at}`` or ``None`` on bad evidence.

    Only durable upstream page captures impose membership requirements.
    Historical/routing-only acceptance has no captured annotation identities.
    """
    query = (
        ub.session.query(ub.KoboAnnotationSeedCapture)
        .filter(
            ub.KoboAnnotationSeedCapture.book_state_id == book_state_id,
            ub.KoboAnnotationSeedCapture.result == "accepted",
            ub.KoboAnnotationSeedCapture.completed_at.isnot(None),
            ub.KoboAnnotationSeedCapture.seed_kind == "upstream_capture",
        )
    )
    if device_id is not None:
        query = query.filter(
            ub.KoboAnnotationSeedCapture.device_id == device_id,
        )
    captures = query.order_by(ub.KoboAnnotationSeedCapture.id.asc()).all()
    requirements = {}
    for capture in captures:
        # Pre-M2/manual rows may have no page evidence. They cannot contribute
        # identities, but neither may invented identities be inferred from a
        # cardinality alone.
        if not capture.pages:
            continue
        try:
            _pages, annotations, _raw_pages = _load_captured_pages(capture.id)
        except Exception:
            return None
        completed_at = _as_utc(capture.completed_at)
        for payload in annotations:
            annotation_id = payload.get("id") if isinstance(payload, dict) else None
            if not isinstance(annotation_id, str) or not annotation_id:
                return None
            prior = requirements.get(annotation_id)
            if prior is None or (
                completed_at is not None and completed_at > prior
            ):
                requirements[annotation_id] = completed_at
    return requirements


def _visible_count(user_id, book_id):
    return (
        ub.session.query(ub.Annotation.id)
        .filter(
            ub.Annotation.user_id == user_id,
            ub.Annotation.book_id == book_id,
            (
                ub.Annotation.hidden.is_(None)
                | (ub.Annotation.hidden == False)  # noqa: E712
            ),
        )
        .count()
    )


def _visible_ids(user_id, book_id):
    return {
        row[0] for row in (
            ub.session.query(ub.Annotation.annotation_id)
            .filter(
                ub.Annotation.user_id == user_id,
                ub.Annotation.book_id == book_id,
                (
                    ub.Annotation.hidden.is_(None)
                    | (ub.Annotation.hidden == False)  # noqa: E712
                ),
            )
            .all()
        )
    }


_ROW_EVIDENCE_FIELDS = (
    "annotation_id", "source", "annotation_type", "highlighted_text",
    "highlight_color", "note_text", "content_id", "start_container_path",
    "start_container_child_index", "start_offset", "end_container_path",
    "end_container_child_index", "end_offset", "context_string",
    "chapter_progress", "cfi_range", "position_type", "pdf_page",
    "pdf_quad_json", "comic_page", "start_xpointer", "end_xpointer",
    "hidden", "client_modified_at", "server_modified_at",
)


def _row_content_sha256(annotation):
    """Hash server-owned generic content for durable reconciliation CAS."""
    if annotation is None:
        return None
    values = []
    for field in _ROW_EVIDENCE_FIELDS:
        value = getattr(annotation, field, None)
        if isinstance(value, datetime):
            value = _as_utc(value)
            value = value.isoformat() if value is not None else None
        values.append(value)
    encoded = json.dumps(
        values, ensure_ascii=True, separators=(",", ":"), default=str,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _snapshot_row_baselines(capture, annotations, *, user_id, book_id):
    """Persist the first server revision/digest seen for every captured id."""
    annotation_ids = {
        payload.get("id") for payload in annotations
        if isinstance(payload, dict)
        and isinstance(payload.get("id"), str)
        and payload.get("id")
    }
    if not annotation_ids:
        return
    existing = {
        row[0] for row in (
            ub.session.query(ub.KoboAnnotationSeedRowBaseline.annotation_key)
            .filter(
                ub.KoboAnnotationSeedRowBaseline.seed_capture_id == capture.id,
                ub.KoboAnnotationSeedRowBaseline.annotation_key.in_(annotation_ids),
            )
            .all()
        )
    }
    rows = (
        ub.session.query(ub.Annotation)
        .filter(
            ub.Annotation.user_id == user_id,
            ub.Annotation.book_id == book_id,
            ub.Annotation.annotation_id.in_(annotation_ids),
        )
        .all()
    )
    by_key = {row.annotation_id: row for row in rows}
    for annotation_key in annotation_ids - existing:
        annotation = by_key.get(annotation_key)
        revision = getattr(annotation, "content_revision", 0) if annotation is not None else 0
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            revision = 0
        ub.session.add(ub.KoboAnnotationSeedRowBaseline(
            seed_capture_id=capture.id,
            annotation_key=annotation_key,
            annotation_row_id=getattr(annotation, "id", None),
            content_revision=revision,
            content_sha256=_row_content_sha256(annotation),
        ))


def _baseline_allows_insert(baseline, annotation):
    """True only when this capture durably observed the row as absent."""
    return bool(
        baseline is not None
        and baseline.annotation_row_id is None
        and baseline.content_revision == 0
        and baseline.content_sha256 is None
        and annotation is None
    )


def _baseline_still_matches(baseline, annotation):
    """Server-owned CAS check; client timestamps intentionally play no role."""
    if baseline is None or annotation is None:
        return False
    return bool(
        baseline.annotation_row_id == annotation.id
        and baseline.content_revision == annotation.content_revision
        and baseline.content_sha256 == _row_content_sha256(annotation)
    )


def _captured_opaque_content_status(annotations):
    """Classify opaque attachment evidence from one complete upstream set."""
    for annotation in annotations:
        if "attachments" not in annotation:
            continue
        attachments = annotation.get("attachments")
        if not isinstance(attachments, dict) or attachments:
            return "present", "wire_attachments"
    return "absent", "wire_attachments_verified"


def _set_failure(capture_id, reason, *, quarantine, log):
    reason = reason if reason in _SAFE_FAILURE_REASONS else "seed_response_invalid"
    capture = ub.session.get(ub.KoboAnnotationSeedCapture, capture_id)
    if capture is None or capture.result != "pending":
        return False
    state = capture.book_state
    capture.completed_at = _now()
    capture.page_count = len(capture.pages)
    capture.result = "rejected" if quarantine else "failed"
    capture.failure_reason = reason
    # A missing/new device contributes only device-scoped capture evidence.
    # Once this book has ever been authoritative, one device's bad cloud page
    # cannot revoke the shared local set for devices already using it.
    global_quarantine = quarantine and not state.ever_authoritative
    if global_quarantine:
        state.authority_status = "quarantined"
        state.quarantine_reason = reason
    elif state.authority_status == "seeding" and not state.ever_authoritative:
        state.authority_status = "unseeded"
    committed = ub.session_commit()
    kobo_annotation_stage0.record_event(
        "seed_capture", "quarantined" if global_quarantine else "failed",
        user_id=state.user_id, book_id=state.book_id,
    )
    if committed is False:
        log.error(
            "Kobo annotation seed failure state did not commit capture_id=%s",
            capture_id,
        )
    return committed


def seed_coverage(*, user_id, book_state_id):
    """Return active-Kobo capture coverage for mixed-authority diagnostics."""
    active_ids = {
        row[0] for row in (
            ub.session.query(ub.Device.id)
            .filter(
                ub.Device.user_id == user_id,
                ub.Device.kind == "kobo",
                ub.Device.active == True,  # noqa: E712
            )
            .all()
        )
    }
    accepted_ids = {
        row[0] for row in (
            ub.session.query(ub.KoboAnnotationSeedCapture.device_id)
            .filter(
                ub.KoboAnnotationSeedCapture.book_state_id == book_state_id,
                ub.KoboAnnotationSeedCapture.result == "accepted",
                ub.KoboAnnotationSeedCapture.completed_at.isnot(None),
                ub.KoboAnnotationSeedCapture.device_id.in_(active_ids),
            )
            .distinct()
            .all()
        )
    } if active_ids else set()
    missing = active_ids - accepted_ids
    return {
        "active_device_count": len(active_ids),
        "accepted_device_count": len(accepted_ids),
        "missing_device_count": len(missing),
        "consistently_local": not missing,
        "books_partially_seeded": int(bool(accepted_ids and missing)),
    }


def accept_routing_only_seed(*, user_id, book_id, device_id, log):
    """Commit device coverage before an ever-authoritative local GET.

    Kobo's cloud is stale after local PATCH acknowledgements begin, so a new
    device must receive the local complete set. Its acceptance evidence is
    routing-only; it must not start a fresh upstream reconciliation.
    """
    try:
        state = _state_for_book(user_id, book_id)
        if state is None or not state.ever_authoritative:
            return False
        if _requesting_device(user_id, device_id) is None:
            return False
        if _accepted_capture(state.id, device_id) is not None:
            return True
        pending = _pending_capture(state.id)
        if pending is not None:
            _retire_pending(pending, "seed_capture_superseded")
        now = _now()
        ub.session.add(ub.KoboAnnotationSeedCapture(
            book_state_id=state.id,
            device_id=device_id,
            started_at=now,
            started_authority_revision=state.authority_revision or 0,
            completed_at=now,
            annotation_count=_visible_count(user_id, book_id),
            page_count=0,
            result="accepted",
            seed_kind="routing_only",
        ))
        return ub.session_commit()
    except Exception:
        ub.session.rollback()
        log.exception(
            "Kobo routing-only seed could not persist user_id=%s book_id=%s",
            user_id, book_id,
        )
        return False


def rebuild_authoritative_device_evidence(*, user_id, book_id, device_id, log):
    """Replace unreadable historical capture proof with current live proof.

    The authoritative generic rows are the monotonic store. Corrupt compressed
    capture bytes are diagnostics, not permission to emit 5xx or resume a stale
    Kobo replacement set. Retire only this device's unreadable evidence and
    commit routing-only proof before the caller renders the live set.
    """
    try:
        state = _state_for_book(user_id, book_id)
        if state is None or not state.ever_authoritative:
            return False
        captures = (
            ub.session.query(ub.KoboAnnotationSeedCapture)
            .filter(
                ub.KoboAnnotationSeedCapture.book_state_id == state.id,
                ub.KoboAnnotationSeedCapture.device_id == device_id,
                ub.KoboAnnotationSeedCapture.result == "accepted",
                ub.KoboAnnotationSeedCapture.seed_kind == "upstream_capture",
            )
            .all()
        )
        rebuilt = False
        for capture in captures:
            invalid = False
            try:
                _pages, annotations, _raw_pages = _load_captured_pages(capture.id)
                invalid = any(
                    not isinstance(payload, dict)
                    or not isinstance(payload.get("id"), str)
                    or not payload.get("id")
                    for payload in annotations
                )
            except Exception:
                invalid = True
            if not invalid:
                continue
            capture.result = "failed"
            capture.failure_reason = "seed_response_invalid"
            rebuilt = True
        if not rebuilt:
            return False
        state.quarantine_reason = "capture_evidence_rebuilt_live"
        ub.session.flush()
        return accept_routing_only_seed(
            user_id=user_id, book_id=book_id, device_id=device_id, log=log,
        )
    except Exception:
        ub.session.rollback()
        log.exception(
            "Kobo authoritative capture evidence rebuild failed "
            "user_id=%s book_id=%s", user_id, book_id,
        )
        return False


def recover_quarantined_book(*, user_id, book_id):
    """User-scoped recovery for seed quarantine or surfaced proof conflict."""
    state = _state_for_book(user_id, book_id)
    if state is None:
        return "not_found", None
    recoverable_authoritative_reason = state.quarantine_reason in {
        "capture_evidence_rebuilt_live",
        "seed_row_conflict_local_won",
        "seed_row_conflict_unresolved",
    }
    if state.authority_status != "quarantined" and not (
        state.ever_authoritative
        and state.authority_status == "authoritative"
        and recoverable_authoritative_reason
    ):
        return "conflict", state
    now = _now()
    pending = (
        ub.session.query(ub.KoboAnnotationSeedCapture)
        .filter(
            ub.KoboAnnotationSeedCapture.book_state_id == state.id,
            ub.KoboAnnotationSeedCapture.result == "pending",
        )
        .all()
    )
    for capture in pending:
        capture.result = "failed"
        capture.failure_reason = "seed_capture_superseded"
        capture.completed_at = now
        capture.page_count = len(capture.pages)
    state.authority_status = (
        "authoritative" if state.ever_authoritative else "unseeded"
    )
    state.authority_revision = (state.authority_revision or 0) + 1
    state.quarantine_reason = None
    if ub.session_commit() is False:
        return "db_error", state
    return "ok", state


def _reconcile_and_promote(capture_id, *, book, user, device_id, log):
    capture = ub.session.get(ub.KoboAnnotationSeedCapture, capture_id)
    if capture is None or capture.result != "pending":
        return False
    state = capture.book_state
    if not _capture_revision_is_current(capture):
        return _set_failure(
            capture_id, "seed_authority_revision_changed",
            quarantine=False, log=log,
        )
    try:
        pages, annotations, raw_pages = _load_captured_pages(capture_id)
    except Exception:
        ub.session.rollback()
        return _set_failure(
            capture_id, "seed_response_invalid", quarantine=False, log=log,
        )

    annotation_ids = [
        row.get("id") if isinstance(row, dict) else None for row in annotations
    ]
    if (
        any(not isinstance(value, str) or not value for value in annotation_ids)
        or len(set(annotation_ids)) != len(annotation_ids)
    ):
        return _set_failure(
            capture_id, "seed_duplicate_annotation_id", quarantine=True, log=log,
        )
    if len(pages) > 1:
        capture.annotation_count = len(annotations)
        capture.page_count = len(pages)
        ub.session.flush()
        return _set_failure(
            capture_id, "seed_capture_requires_pagination",
            quarantine=True, log=log,
        )

    try:
        from cps.services import annotation_sync
        from cps.services.kobo_annotation_capture import (
            extract_annotation_materializations,
        )

        raw_by_id = {}
        for raw_page in raw_pages:
            try:
                records = extract_annotation_materializations(
                    raw_page, member_name="annotations",
                )
            except Exception:
                records = []
            raw_by_id.update({record.annotation_id: record for record in records})

        baselines = {
            row.annotation_key: row for row in (
                ub.session.query(ub.KoboAnnotationSeedRowBaseline)
                .filter(
                    ub.KoboAnnotationSeedRowBaseline.seed_capture_id == capture_id,
                )
                .all()
            )
        }
        conflict_ids = []
        cas_conflict_count = 0

        for payload in annotations:
            annotation = (
                ub.session.query(ub.Annotation)
                .filter(
                    ub.Annotation.user_id == user.id,
                    ub.Annotation.book_id == book.id,
                    ub.Annotation.annotation_id == payload["id"],
                )
                .first()
            )
            equivalent_before = bool(
                annotation is not None
                and annotation_sync.kobo_payload_matches_annotation(
                    annotation, payload, book,
                )
            )
            baseline = baselines.get(payload["id"])
            if baseline is None:
                raise ValueError("captured annotation has no server baseline")
            applied = False
            if not equivalent_before and _baseline_allows_insert(
                baseline, annotation,
            ):
                applied_row = annotation_sync._upsert_annotation(
                    ub.session,
                    payload,
                    book,
                    user,
                    origin_device_id=device_id,
                    mark_last_editor=False,
                )
                if applied_row is not None:
                    annotation = applied_row
                    applied = True
            elif not equivalent_before:
                # A divergent row that already existed when the page was
                # persisted is local authority. Even if its client clock ties
                # or lies in the future, upstream arrival order may not replace
                # it. The durable revision+digest baseline also catches a row
                # changed between page commit and reconciliation.
                if not _baseline_still_matches(baseline, annotation):
                    cas_conflict_count += 1
                conflict_ids.append(payload["id"])
            if annotation is None:
                annotation = (
                    ub.session.query(ub.Annotation)
                    .filter(
                        ub.Annotation.user_id == user.id,
                        ub.Annotation.book_id == book.id,
                        ub.Annotation.annotation_id == payload["id"],
                    )
                    .first()
                )
            if annotation is None:
                raise ValueError("captured annotation was not reconciled")
            equivalent_after = annotation_sync.kobo_payload_matches_annotation(
                annotation, payload, book,
            )
            if (
                annotation.origin_device_id is None
                and (applied or equivalent_after)
            ):
                annotation.origin_device_id = device_id
            raw_record = raw_by_id.get(payload["id"])
            if raw_record is not None and (applied or equivalent_after):
                annotation_sync._store_raw_materialization(
                    ub.session,
                    annotation,
                    raw_record,
                    provenance="kobo_cloud_seed",
                    serveable=True,
                    match_content_revision=True,
                )
        ub.session.flush()
    except Exception:
        ub.session.rollback()
        log.exception(
            "Kobo annotation seed reconciliation failed capture_id=%s",
            capture_id,
        )
        return _set_failure(
            capture_id, "seed_response_invalid", quarantine=False, log=log,
        )

    if conflict_ids and not state.ever_authoritative:
        # There is no ordering claim strong enough to make before first
        # authority: the captured device row and the pre-existing server row
        # diverge, while client clocks are explicitly non-authoritative. Keep
        # both durable sources, expose the conflict, and require authenticated
        # recovery/re-seeding instead of promoting an older local row that the
        # next replacement-set GET could write over the device's newer copy.
        capture.annotation_count = len(annotations)
        capture.page_count = len(pages)
        capture.reconciliation_conflict_count = len(conflict_ids)
        ub.session.flush()
        log.warning(
            "Kobo annotation seed promotion blocked by unresolved row conflict "
            "user_id=%s book_id=%s conflict_count=%s cas_conflict_count=%s",
            user.id, book.id, len(conflict_ids), cas_conflict_count,
        )
        return _set_failure(
            capture_id, "seed_row_conflict_unresolved",
            quarantine=True, log=log,
        )

    visible_ids = _visible_ids(user.id, book.id)
    visible_count = len(visible_ids)
    captured_count = len(annotations)
    refusal_reason = None
    if visible_count < captured_count:
        refusal_reason = "seed_local_count_below_capture"
    elif not set(annotation_ids).issubset(visible_ids):
        refusal_reason = "seed_local_set_missing_captured_id"
    elif visible_count > LOCAL_PAGE_CAPACITY:
        refusal_reason = "seed_local_set_requires_pagination"
    if refusal_reason is not None:
        capture.annotation_count = captured_count
        capture.page_count = len(pages)
        ub.session.flush()
        return _set_failure(capture_id, refusal_reason, quarantine=True, log=log)

    # Close the page-commit/reconcile window: any concurrent authority move
    # invalidates this capture even if its annotation membership still looks
    # plausible at this instant.
    if not _capture_revision_is_current(capture):
        return _set_failure(
            capture_id, "seed_authority_revision_changed",
            quarantine=False, log=log,
        )

    normalized_content_id = _normalized_book_uuid(book)
    conflict = None
    if normalized_content_id is not None:
        conflict = (
            ub.session.query(ub.KoboAnnotationBookState.id)
            .filter(
                ub.KoboAnnotationBookState.user_id == user.id,
                ub.KoboAnnotationBookState.content_id == normalized_content_id,
                ub.KoboAnnotationBookState.id != state.id,
            )
            .first()
        )
    if normalized_content_id is None or conflict is not None:
        return _set_failure(
            capture_id, "seed_content_id_conflict", quarantine=True, log=log,
        )

    now = _now()
    state.content_id = normalized_content_id
    if state.generation_id is None:
        state.generation_id = str(uuid.uuid4())
    state.authority_status = "authoritative"
    state.authority_revision = (state.authority_revision or 0) + 1
    state.ever_authoritative = True
    state.seeded_at = now
    state.quarantine_reason = (
        "seed_row_conflict_local_won" if conflict_ids else None
    )
    state.upstream_seed_etag = capture.upstream_etag
    captured_opaque_status, captured_opaque_source = (
        _captured_opaque_content_status(annotations)
    )
    # The durable guard/trigger owns the one-way `present` invariant. Avoid an
    # attempted downgrade here as well so the intended state is explicit even
    # on databases whose trigger installation is being diagnosed.
    if state.opaque_content_status != "present":
        state.opaque_content_status = captured_opaque_status
        state.opaque_content_source = captured_opaque_source
        state.opaque_content_checked_at = now
    capture.annotation_count = captured_count
    capture.page_count = len(pages)
    capture.completed_at = now
    capture.result = "accepted"
    capture.failure_reason = (
        "seed_row_conflict_local_won" if conflict_ids else None
    )
    capture.reconciliation_conflict_count = len(conflict_ids)
    capture.seed_kind = "upstream_capture"
    if ub.session_commit() is False:
        return False

    coverage = seed_coverage(user_id=user.id, book_state_id=state.id)
    if conflict_ids:
        log.warning(
            "Kobo annotation seed retained divergent local rows "
            "user_id=%s book_id=%s conflict_count=%s cas_conflict_count=%s",
            user.id, book.id, len(conflict_ids), cas_conflict_count,
        )
    log.info(
        "Kobo annotation seed accepted user_id=%s book_id=%s "
        "annotation_count=%s page_count=%s books_partially_seeded=%s "
        "active_device_count=%s accepted_device_count=%s missing_device_count=%s",
        user.id,
        book.id,
        captured_count,
        len(pages),
        coverage["books_partially_seeded"],
        coverage["active_device_count"],
        coverage["accepted_device_count"],
        coverage["missing_device_count"],
    )
    kobo_annotation_stage0.record_event(
        "seed_capture", "accepted", user_id=user.id, book_id=book.id,
        annotation_count=captured_count,
    )
    return True


def record_proxy_response(
    capture_id, *, response, book, user, device_id, request_offset_token, log,
):
    """Persist one upstream page and finalize a complete capture best-effort."""
    if capture_id is None:
        return False
    try:
        capture = ub.session.get(ub.KoboAnnotationSeedCapture, capture_id)
        if capture is None or capture.result != "pending":
            return False
        if not _capture_revision_is_current(capture):
            return _set_failure(
                capture_id, "seed_authority_revision_changed",
                quarantine=False, log=log,
            )
        if response.status_code < 200 or response.status_code >= 300:
            return _set_failure(
                capture_id, "seed_response_invalid", quarantine=False, log=log,
            )
        raw_body = response.get_data()
        parsed = _parse_page(raw_body)
        if parsed is None:
            return _set_failure(
                capture_id, "seed_response_invalid", quarantine=False, log=log,
            )
        annotations, next_offset = parsed
        if next_offset is not None and next_offset == request_offset_token:
            return _set_failure(
                capture_id, "seed_response_invalid", quarantine=False, log=log,
            )

        existing = (
            ub.session.query(ub.KoboAnnotationSeedCapturePage)
            .filter(
                ub.KoboAnnotationSeedCapturePage.seed_capture_id == capture_id,
                ub.KoboAnnotationSeedCapturePage.request_offset_token
                == request_offset_token,
            )
            .first()
        )
        digest = hashlib.sha256(raw_body).hexdigest()
        if existing is None:
            page_number = (
                ub.session.query(ub.KoboAnnotationSeedCapturePage.id)
                .filter(
                    ub.KoboAnnotationSeedCapturePage.seed_capture_id == capture_id,
                )
                .count()
            )
            page = ub.KoboAnnotationSeedCapturePage(
                seed_capture_id=capture_id,
                page_number=page_number,
                request_offset_token=request_offset_token,
                response_body_gzip=gzip.compress(raw_body, mtime=0),
                response_sha256=digest,
                response_etag=response.headers.get("ETag"),
                next_offset_token=next_offset,
            )
            ub.session.add(page)
        elif existing.response_sha256 != digest:
            return _set_failure(
                capture_id, "seed_response_invalid", quarantine=False, log=log,
            )

        capture.upstream_etag = response.headers.get("ETag") or capture.upstream_etag
        capture.response_sha256 = digest
        _snapshot_row_baselines(
            capture, annotations, user_id=user.id, book_id=book.id,
        )
        ub.session.flush()
        if ub.session_commit() is False:
            return False
        if next_offset is not None:
            return True
        return _reconcile_and_promote(
            capture_id, book=book, user=user, device_id=device_id, log=log,
        )
    except Exception:
        ub.session.rollback()
        log.exception(
            "Kobo annotation seed page persistence failed capture_id=%s",
            capture_id,
        )
        return _set_failure(
            capture_id, "seed_response_invalid", quarantine=False, log=log,
        )
