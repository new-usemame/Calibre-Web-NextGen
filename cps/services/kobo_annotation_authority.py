# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render CWNG's complete owned-book annotation set for Kobo Nickel.

Owned annotation GETs are replacement-set operations on the device: a partial,
empty-on-error, or non-success response can destroy Nickel's local rows.  This
module therefore has one non-negotiable invariant: every visible database row
is represented in the returned array.  Exact Kobo materializations win.  When
one is absent or stale, the generic columns are mapped conservatively; an
imperfect mapping is logged and included, never silently omitted.

``KoboAnnotationMaterialization.serveable`` is intentionally not consulted for
``provenance='kobo_patch'`` here.  That flag answers whether a delta is safe to
promote into an independently authoritative/cloud-seeded set; it does not make
the authenticated user's own, byte-exact upload unsafe to replay to that same
user.  Refusing it here would replace a device row with less faithful columns.

An owned annotations GET must not manufacture an empty replacement set from a
read failure. Nickel has been observed treating errors destructively too, so a
validated snapshot of CWNG's last exact complete response is replayed when the
live set cannot be read. With no snapshot at all, a loud 503 is the terminal
ever-authoritative fallback; it is still safer than claiming unknown means
empty or proxying Kobo's known-stale copy.

This renderer is used only after Stage 0 proves that the authenticated device's
complete set was accepted and the book state is ``authoritative`` (the stored
spelling of "fully seeded").  GET and PATCH use the same proof gate: until that
proof exists, both continue through Kobo so a partial local set cannot replace
the device set and new uploads continue feeding Kobo's more-complete copy.

Before authority, the complete current set must fit in the requested page.
After authority, local PATCH acknowledgements make Kobo's copy stale, so a set
that grows beyond 100 is returned complete in one final page and surfaced as
``oversize_single_page``. This Nickel behavior is ASSUMED pending the Clara
hardware A/B; it is the only current policy that neither truncates local data
nor proxies a stale replacement set.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from cps import ub
from cps.services.annotation_colors import KOBO_BOOKMARK_COLOR_HEX, to_storage_color
from cps.services.annotation_types import to_storage_type
from cps.services.kobo_annotation_capture import project_exact_materialization


_KOBO_WIRE_COLORS = frozenset(KOBO_BOOKMARK_COLOR_HEX.values())
_EPOCH = "1970-01-01T00:00:00.000Z"
_BOOK_STATE_CONTENT_ID_LIMIT = 64
_LOCAL_PAGE_CAPACITY = 100
_SKIP_LOGGED_BOOKS = set()
_SKIP_LOG_LOCK = threading.Lock()
AUTHORITY_EVER = "ever_authoritative"
AUTHORITY_NEVER = "never_authoritative"
AUTHORITY_LOOKUP_FAILED = "lookup_failed"
STICKY_GET_LOCAL = "local"
STICKY_GET_SNAPSHOT = "snapshot"


def _blob(value):
    if isinstance(value, memoryview):
        return value.tobytes()
    return value if isinstance(value, bytes) else None


def _utc_timestamp(value):
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _chapter_filename(annotation, entitlement_id):
    content_id = annotation.content_id
    if not isinstance(content_id, str) or "!!" not in content_id:
        return None
    book_part, chapter = content_id.split("!!", 1)
    if not chapter:
        return None
    if book_part.strip().strip("{}").casefold() != entitlement_id.strip().strip("{}").casefold():
        return None
    return chapter


def _span(annotation, entitlement_id, *, dogear=False):
    chapter = _chapter_filename(annotation, entitlement_id)
    progress = annotation.chapter_progress
    start_path = annotation.start_container_path
    end_path = annotation.end_container_path
    start_offset = annotation.start_offset
    end_offset = annotation.end_offset
    if (
        chapter is None
        or isinstance(progress, bool)
        or not isinstance(progress, (int, float))
        or not math.isfinite(progress)
        or not 0 <= progress <= 1
        or not isinstance(start_path, str)
        or not start_path
        or not isinstance(end_path, str)
        or not end_path
        or isinstance(start_offset, bool)
        or not isinstance(start_offset, int)
        or start_offset < 0
        or isinstance(end_offset, bool)
        or not isinstance(end_offset, int)
        or end_offset < 0
        or (start_path == end_path and start_offset > end_offset)
    ):
        return None
    span = {
        "chapterFilename": chapter,
        "chapterProgress": progress,
        "endChar": end_offset,
        "endPath": end_path,
        "startChar": start_offset,
        "startPath": start_path,
    }
    if dogear:
        # The measured dogear shape contains chapterTitle.  The generic schema
        # predates that field, so an empty title is the only honest value when
        # no raw Kobo object survived; the positional identity is still exact.
        span["chapterTitle"] = ""
    return span


def _exact_raw(annotation, materialization):
    if materialization is None:
        return None
    # A cloud-seed sidecar is authoritative only when reconciliation proved
    # that its captured content was applied or already content-equivalent.
    # PATCH sidecars retain their existing authenticated-user replay policy.
    if (
        materialization.provenance == "kobo_cloud_seed"
        and not materialization.serveable
    ):
        return None
    # A generic-column edit advances content_revision without rewriting the
    # Kobo sidecar.  Replaying that stale object would resurrect the old edit.
    if materialization.materialization_revision != annotation.content_revision:
        return None
    raw_object = _blob(materialization.raw_annotation_json)
    raw_location = _blob(materialization.raw_location_json)
    if raw_object is None or raw_location is None:
        return None
    if hashlib.sha256(raw_object).hexdigest() != materialization.payload_sha256:
        return None
    try:
        projected = project_exact_materialization(raw_object, raw_location)
        parsed = json.loads(projected)
    except Exception:
        return None
    if not isinstance(parsed, dict) or parsed.get("id") != annotation.annotation_id:
        return None
    return projected


def _fallback_object(annotation, entitlement_id):
    """Return ``(object, faithful, reason)`` without ever dropping the row."""
    reasons = []
    native_type = to_storage_type(annotation.annotation_type)
    if native_type not in {"highlight", "dogear", "note"}:
        reasons.append("unknown_annotation_type")
        native_type = native_type or "note"

    dogear = native_type == "dogear"
    span = _span(annotation, entitlement_id, dogear=dogear)
    if span is None:
        reasons.append("incomplete_kobo_location")

    timestamp = (
        _utc_timestamp(annotation.client_modified_at)
        or _utc_timestamp(annotation.server_modified_at)
        or _utc_timestamp(annotation.created_at)
    )
    if timestamp is None:
        reasons.append("missing_timestamp")
        timestamp = _EPOCH

    common = {
        "clientLastModifiedUtc": timestamp,
        "context": annotation.context_string or "",
        "highlightedText": annotation.highlighted_text or "",
        "id": annotation.annotation_id,
        "location": {"span": span} if span is not None else {},
        "type": native_type,
    }
    if dogear:
        # Generic Annotation rows do not retain chapterTitle.  Keep the exact
        # field set and positional identity, but report the missing value rather
        # than pretending the empty placeholder is byte-faithful.
        reasons.append("dogear_chapter_title_unavailable")
        if common["highlightedText"]:
            reasons.append("dogear_has_highlighted_text")
        if annotation.highlight_color is not None:
            reasons.append("dogear_has_color")
        if annotation.note_text not in (None, ""):
            # Preserve content even though tonight's measured dogear did not
            # carry this member.
            common["noteText"] = annotation.note_text
            reasons.append("dogear_has_note")
        return common, not reasons, ",".join(reasons)

    result = {
        "attachments": {},
        "clientLastModifiedUtc": common["clientLastModifiedUtc"],
        "context": common["context"],
        "highlightedText": common["highlightedText"],
        "id": common["id"],
        "location": common["location"],
        "type": common["type"],
    }
    if annotation.note_text is not None:
        result["noteText"] = annotation.note_text

    if native_type == "note":
        # Native notes exist in the measured device DB, but no column-built note
        # object has yet been accepted on Nickel's wire.  It is still included
        # so its id and text cannot disappear from a replacement set.
        reasons.append("note_wire_shape_unproven")

    color = to_storage_color(annotation.highlight_color)
    if color in _KOBO_WIRE_COLORS:
        result["highlightColor"] = color
    elif native_type == "highlight":
        # Keep a future/foreign token rather than inventing a measured colour.
        # Nickel may understand a newer palette even when this version does not.
        result["highlightColor"] = color
        reasons.append("unrecognized_or_missing_highlight_color")
    elif color is not None:
        result["highlightColor"] = color
        reasons.append("note_has_unproven_color")

    if native_type == "highlight" and not common["highlightedText"]:
        reasons.append("highlight_text_empty")
    if native_type == "note" and annotation.note_text in (None, ""):
        reasons.append("note_text_empty")
    return result, not reasons, ",".join(reasons)


def _annotation_rows(user_id, book_id, page_limit):
    return (
        ub.session.query(ub.Annotation, ub.KoboAnnotationMaterialization)
        .outerjoin(
            ub.KoboAnnotationMaterialization,
            ub.KoboAnnotationMaterialization.annotation_id == ub.Annotation.id,
        )
        .filter(
            ub.Annotation.user_id == user_id,
            ub.Annotation.book_id == book_id,
            (
                ub.Annotation.hidden.is_(None)
                | (ub.Annotation.hidden == False)  # noqa: E712 - SQLAlchemy expression
            ),
        )
        .order_by(ub.Annotation.annotation_id.collate("BINARY").asc())
        .limit(page_limit + 1)
        .all()
    )


def _simple_annotation_rows(user_id, book_id, page_limit):
    """Read visible rows without the optional materialization join."""
    return [
        (annotation, None)
        for annotation in (
            ub.session.query(ub.Annotation)
            .filter(
                ub.Annotation.user_id == user_id,
                ub.Annotation.book_id == book_id,
                (
                    ub.Annotation.hidden.is_(None)
                    | (ub.Annotation.hidden == False)  # noqa: E712
                ),
            )
            .order_by(ub.Annotation.annotation_id.collate("BINARY").asc())
            .limit(page_limit + 1)
            .all()
        )
    ]


def _normalized_entitlement_id(entitlement_id):
    """Mirror ``resolve_entitlement_ownership``'s safe lookup spelling."""
    if not isinstance(entitlement_id, str):
        return ""
    return entitlement_id.strip().strip("{}").strip().casefold()


def _safe_rollback():
    try:
        ub.session.rollback()
    except Exception:
        pass


def _failure_reason(stage, error):
    # Exception messages can contain annotation text or raw SQL parameters.
    # The class is enough to diagnose the failed stage without logging either.
    return f"{stage}_{type(error).__name__}"


def _transient_generation(user_id, book_id, normalized_content_id):
    # JSON's ASCII escaping keeps even malformed Unicode out of uuid5's UTF-8
    # encoder, so an unusable request id cannot defeat the emergency ETag.
    seed = json.dumps(
        ["cwng:kobo-annotations", user_id, book_id, normalized_content_id],
        ensure_ascii=True, separators=(",", ":"), default=str,
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def _transient_etag(user_id, book_id, normalized_content_id, digest):
    # When state cannot be persisted, make both the generation and revision a
    # function of stable request identity + returned bytes.  The same degraded
    # set gets the same ETag and any set change necessarily changes both the
    # digest suffix and numeric revision (subject only to SHA-256 collision).
    revision = int(digest, 16) or 1
    return 'W/"CWNG:{}:{}:{}"'.format(
        _transient_generation(user_id, book_id, normalized_content_id),
        revision,
        digest[:16],
    )


def _state_for_book(user_id, book_id):
    return (
        ub.session.query(ub.KoboAnnotationBookState)
        .filter(
            ub.KoboAnnotationBookState.user_id == user_id,
            ub.KoboAnnotationBookState.book_id == book_id,
        )
        .first()
    )


def _state_for_content(user_id, normalized_content_id):
    return (
        ub.session.query(ub.KoboAnnotationBookState)
        .filter(
            ub.KoboAnnotationBookState.user_id == user_id,
            ub.KoboAnnotationBookState.content_id == normalized_content_id,
        )
        .first()
    )


def _log_authority_skip_once(log, *, user_id, book_id, reason):
    """Log one structural INFO per user/book for this process lifetime."""
    key = (user_id, book_id)
    with _SKIP_LOG_LOCK:
        if key in _SKIP_LOGGED_BOOKS:
            return
        _SKIP_LOGGED_BOOKS.add(key)
    try:
        log.info(
            "Kobo local annotation authority skipped user_id=%s book_id=%s "
            "reason=%s",
            user_id, book_id, reason,
        )
    except Exception:
        pass


def reset_skip_log_for_testing():
    with _SKIP_LOG_LOCK:
        _SKIP_LOGGED_BOOKS.clear()


def _accepted_device_annotation_count(book_state_id, device_id):
    """Return the latest accepted complete-seed count for this device/book."""
    if not isinstance(device_id, int) or isinstance(device_id, bool):
        return None
    capture = (
        ub.session.query(ub.KoboAnnotationSeedCapture)
        .filter(
            ub.KoboAnnotationSeedCapture.book_state_id == book_state_id,
            ub.KoboAnnotationSeedCapture.device_id == device_id,
            ub.KoboAnnotationSeedCapture.result == "accepted",
            ub.KoboAnnotationSeedCapture.completed_at.isnot(None),
            ub.KoboAnnotationSeedCapture.annotation_count.isnot(None),
        )
        .order_by(
            ub.KoboAnnotationSeedCapture.completed_at.desc(),
            ub.KoboAnnotationSeedCapture.id.desc(),
        )
        .first()
    )
    count = getattr(capture, "annotation_count", None)
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return None
    return count


def _visible_annotation_count(user_id, book_id):
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


def ever_authoritative(user_id, book_id):
    """Return tri-state authority history; lookup failure is never false."""
    try:
        state = _state_for_book(user_id, book_id)
        if state is not None and state.ever_authoritative:
            return AUTHORITY_EVER
        return AUTHORITY_NEVER
    except Exception:
        _safe_rollback()
        return AUTHORITY_LOOKUP_FAILED


def authority_evidence_for_route(user_id, book_id):
    """Independent state read used only after an ambiguous boundary lookup."""
    try:
        state = _state_for_book(user_id, book_id)
        if state is not None and state.ever_authoritative:
            return AUTHORITY_EVER
        return AUTHORITY_NEVER
    except Exception:
        _safe_rollback()
        return AUTHORITY_LOOKUP_FAILED


def sticky_render_page_limit(user_id, book_id, requested_limit):
    """Return a complete local bound after Kobo's cloud becomes stale.

    ASSUMED pending Clara hardware verification: Nickel accepts a complete
    oversized final page. Truncation, 5xx, and stale proxying are all known to
    violate the replacement-set invariant, so post-authority rows remain
    losslessly available even when the request says ``limit=100``.
    """
    try:
        visible_count = _visible_annotation_count(user_id, book_id)
    except Exception:
        _safe_rollback()
        visible_count = _LOCAL_PAGE_CAPACITY
    base = requested_limit if isinstance(requested_limit, int) else _LOCAL_PAGE_CAPACITY
    return max(1, base, visible_count)


def mark_authoritative_oversize(user_id, book_id, *, log, commit=True):
    """Surface lossless post-authority growth without rejecting the PATCH.

    ``commit=False`` stages the classification in the caller's transaction.
    """
    try:
        state = _state_for_book(user_id, book_id)
        if state is None or not (
            state.ever_authoritative
            or state.authority_status == "authoritative"
        ):
            return False
        visible_count = _visible_annotation_count(user_id, book_id)
        if visible_count > _LOCAL_PAGE_CAPACITY:
            state.quarantine_reason = "oversize_single_page"
        elif state.quarantine_reason == "oversize_single_page":
            state.quarantine_reason = None
        if not commit:
            ub.session.flush()
            return True
        return ub.session_commit()
    except Exception:
        if not commit:
            raise
        _safe_rollback()
        log.exception(
            "Kobo authoritative oversize state update failed "
            "user_id=%s book_id=%s", user_id, book_id,
        )
        return False


def advance_authoritative_patch_revision(user_id, book_id, *, log, commit=True):
    """Invalidate prior rendered bytes before acknowledging a local PATCH.

    ``commit=False`` stages this mutation beside the annotation mutations in a
    caller-owned request transaction. ``set_digest`` and the current ETag are
    cleared because no complete post-PATCH body has yet been rendered; the next
    successful GET will bind them to exact response bytes.
    """
    try:
        state = _state_for_book(user_id, book_id)
        if state is None or not (
            state.ever_authoritative
            or state.authority_status == "authoritative"
        ):
            return False
        state.authority_revision = (state.authority_revision or 0) + 1
        state.set_digest = None
        state.current_etag = None
        state.etag_kind = None
        state.last_mutation_at = datetime.now(timezone.utc)
        if not commit:
            ub.session.flush()
            return True
        committed = ub.session_commit()
        if committed is False:
            log.error(
                "Kobo authoritative PATCH revision did not commit "
                "user_id=%s book_id=%s", user_id, book_id,
            )
        return committed
    except Exception:
        if not commit:
            raise
        _safe_rollback()
        log.exception(
            "Kobo authoritative PATCH revision failed "
            "user_id=%s book_id=%s", user_id, book_id,
        )
        return False


def _captured_membership_is_safe(
    *, state, user_id, book_id, device_id, visible_ids,
):
    """Prove captured identities are visible or have newer local tombstones."""
    from cps.services.kobo_annotation_seeding import accepted_identity_requirements

    requirements = accepted_identity_requirements(
        state.id,
        # Before first authority, this exact device's capture is the proof.
        # Afterwards all upstream captures are shared baseline evidence.
        device_id=None if state.ever_authoritative else device_id,
    )
    if requirements is None:
        return False
    return _requirements_membership_is_safe(
        requirements=requirements,
        user_id=user_id,
        book_id=book_id,
        visible_ids=visible_ids,
    )


def _requirements_membership_is_safe(
    *, requirements, user_id, book_id, visible_ids,
):
    """Prove one already-loaded identity requirement map against live rows."""
    missing = set(requirements) - set(visible_ids)
    if not missing:
        return True
    rows = (
        ub.session.query(ub.Annotation)
        .filter(
            ub.Annotation.user_id == user_id,
            ub.Annotation.book_id == book_id,
            ub.Annotation.annotation_id.in_(missing),
        )
        .all()
    )
    by_id = {row.annotation_id: row for row in rows}
    for annotation_id in missing:
        row = by_id.get(annotation_id)
        captured_at = requirements[annotation_id]
        modified_at = row.server_modified_at if row is not None else None
        if modified_at is not None and modified_at.tzinfo is None:
            modified_at = modified_at.replace(tzinfo=timezone.utc)
        elif modified_at is not None:
            modified_at = modified_at.astimezone(timezone.utc)
        if (
            row is None
            or not bool(row.hidden)
            or captured_at is None
            or modified_at is None
            or modified_at <= captured_at
        ):
            return False
    return True


def prepare_authoritative_device_get(
    *, user_id, book_id, device_id, log,
):
    """Commit device evidence before an ever-authoritative local response.

    Hardware-observed lifecycle boundary: KOBO-HIGHLIGHTS-STATE.md §6p shows
    this GET is issued by download/re-download after Nickel's per-book rows
    have been emptied. A fresh/reset device is therefore safe to hydrate from
    CWNG's monotonic live set. An earlier PATCH is already in that set. The
    residual offline-edit-without-upload case is byte-identical to Kobo cloud
    status quo. A prior CWNG ETag proves possession of OUR prior replacement
    set, so it strengthens the local path: Kobo's now-stale copy must never be
    proxied back over it. Any inability to establish fresh live proof requests
    the last complete CWNG snapshot, never the upstream route.
    """
    try:
        state = _state_for_book(user_id, book_id)
        if state is None or not state.ever_authoritative:
            return STICKY_GET_SNAPSHOT
        device = (
            ub.session.query(ub.Device.id)
            .filter(
                ub.Device.id == device_id,
                ub.Device.user_id == user_id,
                ub.Device.kind == "kobo",
            )
            .first()
        )
        if device is None:
            return STICKY_GET_SNAPSHOT

        accepted = (
            ub.session.query(ub.KoboAnnotationSeedCapture)
            .filter(
                ub.KoboAnnotationSeedCapture.book_state_id == state.id,
                ub.KoboAnnotationSeedCapture.device_id == device_id,
                ub.KoboAnnotationSeedCapture.result == "accepted",
                ub.KoboAnnotationSeedCapture.completed_at.isnot(None),
            )
            .all()
        )
        if not accepted:
            from cps.services.kobo_annotation_seeding import accept_routing_only_seed
            return (
                STICKY_GET_LOCAL
                if accept_routing_only_seed(
                    user_id=user_id, book_id=book_id,
                    device_id=device_id, log=log,
                )
                else STICKY_GET_SNAPSHOT
            )

        upstream = [row for row in accepted if row.seed_kind == "upstream_capture"]
        if not upstream:
            return STICKY_GET_LOCAL
        from cps.services.kobo_annotation_seeding import (
            accepted_identity_requirements,
            rebuild_authoritative_device_evidence,
        )
        requirements = accepted_identity_requirements(state.id, device_id=device_id)
        if requirements is None:
            return (
                STICKY_GET_LOCAL
                if rebuild_authoritative_device_evidence(
                    user_id=user_id, book_id=book_id,
                    device_id=device_id, log=log,
                )
                else STICKY_GET_SNAPSHOT
            )
        visible_ids = {
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
        return STICKY_GET_LOCAL if _requirements_membership_is_safe(
            requirements=requirements,
            user_id=user_id,
            book_id=book_id,
            visible_ids=visible_ids,
        ) else STICKY_GET_SNAPSHOT
    except Exception:
        _safe_rollback()
        log.exception(
            "Kobo authoritative device pre-serve proof failed "
            "user_id=%s book_id=%s", user_id, book_id,
        )
        return STICKY_GET_SNAPSHOT


def local_get_is_eligible(*, settings, user, book_id, entitlement_id,
                          page_limit, device_id, log):
    """Gate GET/PATCH as a pair and prove captured identity membership.

    Before first authority, all normal gates and this device's accepted seed
    evidence are required. Once ``ever_authoritative`` is set, Kobo's cloud is
    stale by construction: gate drift or a new device may not split GET back
    to upstream while PATCH remains local.
    """
    user_id = getattr(user, "id", None)
    reason = None
    try:
        state = _state_for_book(user_id, book_id)
        sticky = bool(state is not None and state.ever_authoritative)
        if not isinstance(page_limit, int) or isinstance(page_limit, bool):
            reason = "page_limit_invalid"
        elif page_limit < 1 or (
            page_limit > _LOCAL_PAGE_CAPACITY and not sticky
        ):
            reason = "page_limit_unsupported"

        if reason is None and not sticky:
            from cps.services import kobo_annotation_stage0
            schema_ready = kobo_annotation_stage0.schema_capable(
                ub.session.get_bind(),
            )
            reason = kobo_annotation_stage0.gate_failure_reason(
                settings, user, state, schema_ready=schema_ready,
            )
        elif reason is None and state is None:
            reason = "book_state_missing"
        if reason is None and (
            _normalized_entitlement_id(state.content_id)
            != _normalized_entitlement_id(entitlement_id)
        ):
            reason = "content_id_mismatch"

        declared_count = None
        if reason is None and not sticky:
            declared_count = _accepted_device_annotation_count(state.id, device_id)
            if declared_count is None:
                reason = "accepted_device_seed_count_missing"

        visible_count = None
        if reason is None:
            visible_count = _visible_annotation_count(user_id, book_id)
            if declared_count is not None and visible_count < declared_count:
                reason = "local_count_below_device_seed"
            elif not sticky and visible_count > _LOCAL_PAGE_CAPACITY:
                reason = "local_set_requires_pagination"
            elif visible_count > page_limit:
                reason = "requested_page_too_small"
        if reason is None and not sticky:
            visible_ids = {
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
            if not _captured_membership_is_safe(
                state=state,
                user_id=user_id,
                book_id=book_id,
                device_id=device_id,
                visible_ids=visible_ids,
            ):
                reason = "captured_identity_missing"
    except Exception as error:
        _safe_rollback()
        reason = _failure_reason("authority_gate", error)

    if reason is not None:
        _log_authority_skip_once(
            log, user_id=user_id, book_id=book_id, reason=reason,
        )
        return False
    return True


def _render_count_is_safe(
    *, user_id, book_id, device_id, row_count, row_ids=None,
):
    """Recheck count and captured identity membership at render time."""
    state = _state_for_book(user_id, book_id)
    if state is None or (
        state.authority_status != "authoritative"
        and not state.ever_authoritative
    ):
        return False
    if state.ever_authoritative:
        # Historical capture bytes are no longer the live-set authority. The
        # rows just queried are the complete monotonic store, including later
        # local PATCHes and tombstones; corrupt capture evidence is repaired at
        # the device pre-serve boundary rather than turning this GET into 5xx.
        return True
    if not state.ever_authoritative:
        declared_count = _accepted_device_annotation_count(state.id, device_id)
        if declared_count is None or row_count < declared_count:
            return False
    return _captured_membership_is_safe(
        state=state,
        user_id=user_id,
        book_id=book_id,
        device_id=device_id,
        visible_ids=row_ids or set(),
    )


def _book_state(user_id, book_id, entitlement_id):
    """Race-safe best-effort state lookup/create; never raise to the GET."""
    normalized_content_id = _normalized_entitlement_id(entitlement_id)
    if (
        not normalized_content_id
        or len(normalized_content_id) > _BOOK_STATE_CONTENT_ID_LIMIT
    ):
        return None, normalized_content_id, "book_state_content_id_unusable"

    try:
        state = _state_for_book(user_id, book_id)
        if state is None:
            # Detect the other unique key before attempting the INSERT.  A row
            # for another book is inconsistent state, not ours to claim.
            content_state = _state_for_content(user_id, normalized_content_id)
            if content_state is not None:
                if content_state.book_id == book_id:
                    state = content_state
                else:
                    return None, normalized_content_id, \
                        "book_state_content_conflict"

        if state is None:
            candidate = ub.KoboAnnotationBookState(
                user_id=user_id,
                book_id=book_id,
                content_id=normalized_content_id,
                authority_status="unseeded",
                authority_revision=0,
                generation_id=str(uuid.uuid4()),
                opaque_content_status="unknown",
            )
            try:
                # The SAVEPOINT confines a losing concurrent INSERT.  A bare
                # rollback here could discard unrelated request state.
                with ub.begin_contained_nested(ub.session):
                    ub.session.add(candidate)
                    ub.session.flush()
            except IntegrityError:
                # The winner may have matched either unique key.  Re-read both;
                # if it is not visible yet, use a deterministic transient ETag.
                state = _state_for_book(user_id, book_id)
                if state is None:
                    state = _state_for_content(user_id, normalized_content_id)
                    if state is not None and state.book_id != book_id:
                        state = None
                if state is None:
                    return None, normalized_content_id, \
                        "book_state_insert_IntegrityError"
            except Exception as error:
                return None, normalized_content_id, _failure_reason(
                    "book_state_insert", error,
                )
            else:
                state = candidate

        try:
            if str(uuid.UUID(state.generation_id)) != state.generation_id:
                raise ValueError("non-canonical generation id")
        except (ValueError, TypeError, AttributeError):
            # Deterministic repair also remains stable if its commit fails.
            state.generation_id = _transient_generation(
                user_id, book_id, normalized_content_id,
            )
        return state, normalized_content_id, None
    except Exception as error:
        _safe_rollback()
        return None, normalized_content_id, _failure_reason(
            "book_state_lookup", error,
        )


def _safe_string(annotation, attribute, default=""):
    try:
        value = getattr(annotation, attribute)
    except Exception:
        return default
    if value is None:
        return default
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        return default


def _emergency_object(annotation, entitlement_id):
    """Minimal Kobo-like object that preserves a row's identity and text."""
    native_type = _safe_string(annotation, "annotation_type", "note").casefold()
    if native_type not in {"highlight", "dogear", "note"}:
        native_type = "note"
    timestamp = None
    for attribute in ("client_modified_at", "server_modified_at", "created_at"):
        try:
            timestamp = _utc_timestamp(getattr(annotation, attribute))
        except Exception:
            timestamp = None
        if timestamp is not None:
            break
    try:
        span = _span(annotation, entitlement_id, dogear=native_type == "dogear")
    except Exception:
        span = None

    result = {
        "clientLastModifiedUtc": timestamp or _EPOCH,
        "context": _safe_string(annotation, "context_string"),
        "highlightedText": _safe_string(annotation, "highlighted_text"),
        "id": _safe_string(annotation, "annotation_id"),
        "location": {"span": span} if span is not None else {},
        "type": native_type,
    }
    note_text = _safe_string(annotation, "note_text", default="")
    if native_type != "dogear":
        result["attachments"] = {}
    if note_text or native_type == "note":
        result["noteText"] = note_text
    color = _safe_string(annotation, "highlight_color", default="")
    if color and native_type != "dogear":
        result["highlightColor"] = color
    return result


def _encode_object(value):
    try:
        return json.dumps(
            value, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
    except Exception:
        # Escaping non-ASCII also handles malformed/unpaired Unicode while
        # preserving the JSON string's code points for a best-effort replay.
        return json.dumps(
            value, ensure_ascii=True, separators=(",", ":"), default=str,
        ).encode("ascii")


def _emergency_rows(user_id, book_id, page_limit):
    try:
        return _simple_annotation_rows(user_id, book_id, page_limit), None
    except Exception as error:
        _safe_rollback()
        return [], _failure_reason("emergency_row_query", error)


def _log_degraded(log, *, user_id, book_id, visible_count, reasons):
    if not reasons:
        return
    try:
        log.error(
            "Owned Kobo annotation GET degraded user_id=%s book_id=%s "
            "visible_count=%d reason_count=%d reasons=%s",
            user_id, book_id, visible_count, len(reasons),
            sorted(set(reasons)),
        )
    except Exception:
        # Logging must not be allowed to convert the loss-preventing 200 to a
        # device-wiping error response.
        pass


def _snapshot_payload(state, *, log, user_id, book_id):
    """Return last exact bytes only when bound to the current authority set."""
    compressed = _blob(getattr(state, "last_served_body_gzip", None))
    expected_digest = getattr(state, "last_served_body_sha256", None)
    etag = getattr(state, "last_served_etag", None)
    expected_count = getattr(state, "last_served_annotation_count", None)
    snapshot_revision = getattr(state, "last_served_authority_revision", None)
    snapshot_set_digest = getattr(state, "last_served_set_digest", None)
    current_revision = getattr(state, "authority_revision", None)
    current_set_digest = getattr(state, "set_digest", None)
    if (
        compressed is None
        or not isinstance(expected_digest, str)
        or len(expected_digest) != 64
        or not isinstance(snapshot_set_digest, str)
        or len(snapshot_set_digest) != 64
        or snapshot_set_digest != expected_digest
        or snapshot_set_digest != current_set_digest
        or not isinstance(snapshot_revision, int)
        or isinstance(snapshot_revision, bool)
        or snapshot_revision != current_revision
        or not isinstance(etag, str)
        or not etag.startswith('W/"CWNG:')
        or not isinstance(expected_count, int)
        or isinstance(expected_count, bool)
        or expected_count < 0
    ):
        return None
    try:
        body = gzip.decompress(compressed)
        if hashlib.sha256(body).hexdigest() != expected_digest:
            raise ValueError("snapshot digest mismatch")
        payload = json.loads(body)
        annotations = payload.get("annotations") if isinstance(payload, dict) else None
        if (
            not isinstance(annotations, list)
            or len(annotations) != expected_count
            or payload.get("nextPageOffsetToken") is not None
        ):
            raise ValueError("snapshot is not one complete page")
    except Exception:
        try:
            log.error(
                "Kobo authoritative last-served snapshot invalid "
                "user_id=%s book_id=%s",
                user_id, book_id, exc_info=True,
            )
        except Exception:
            pass
        return None
    return body, etag


def load_last_served_complete_set(*, user_id, book_id, log):
    """Load CWNG's durable complete replacement set without inventing rows."""
    try:
        state = _state_for_book(user_id, book_id)
        if state is None or not state.ever_authoritative:
            return None
        return _snapshot_payload(
            state, log=log, user_id=user_id, book_id=book_id,
        )
    except Exception:
        _safe_rollback()
        try:
            log.critical(
                "Kobo authoritative last-served snapshot lookup failed "
                "user_id=%s book_id=%s",
                user_id, book_id, exc_info=True,
            )
        except Exception:
            pass
        return None


def _commit_complete_render(*, state, body, digest, annotation_count):
    """Advance CWNG revision and atomically persist the exact served bytes."""
    if state.set_digest != digest:
        state.authority_revision = (state.authority_revision or 0) + 1
        state.set_digest = digest
        state.last_mutation_at = datetime.now(timezone.utc)
    revision = max(1, state.authority_revision or 0)
    state.authority_revision = revision
    etag = 'W/"CWNG:{}:{}:{}"'.format(
        state.generation_id, revision, digest[:16],
    )
    state.current_etag = etag
    state.etag_kind = "cwng_revision"
    state.last_served_body_gzip = gzip.compress(body, mtime=0)
    state.last_served_body_sha256 = digest
    state.last_served_etag = etag
    state.last_served_annotation_count = annotation_count
    state.last_served_authority_revision = revision
    state.last_served_set_digest = digest
    state.last_served_at = datetime.now(timezone.utc)
    # State, ETag, and fallback bytes become durable together before Flask can
    # begin sending the replacement-set response.
    ub.session.commit()
    return etag


def _emergency_render(*, user_id, book_id, entitlement_id, page_limit,
                      device_id, log, reason, force_authoritative=False):
    rows, query_reason = _emergency_rows(user_id, book_id, page_limit)
    if query_reason is not None:
        # A failed query supplies no evidence that the visible set is empty.
        # Replaying our own last complete response is the only non-wiping 200.
        _log_degraded(
            log, user_id=user_id, book_id=book_id, visible_count=0,
            reasons=[reason, query_reason, "last_served_snapshot_required"],
        )
        return load_last_served_complete_set(
            user_id=user_id, book_id=book_id, log=log,
        )
    if len(rows) > page_limit:
        return None
    if not force_authoritative and not _render_count_is_safe(
        user_id=user_id, book_id=book_id, device_id=device_id,
        row_count=len(rows),
        row_ids={annotation.annotation_id for annotation, _ in rows},
    ):
        return None
    reasons = [reason]
    if query_reason is not None:
        reasons.append(query_reason)
    objects = []
    for annotation, _materialization in rows:
        try:
            objects.append(_encode_object(_emergency_object(annotation, entitlement_id)))
        except Exception as error:
            # Keep one member per visible row even if an exotic ORM value also
            # defeats the emergency mapper.  Identity/text getters are each
            # isolated so this object cannot inherit the original exception.
            objects.append(_encode_object({
                "attachments": {},
                "clientLastModifiedUtc": _EPOCH,
                "context": "",
                "highlightedText": _safe_string(annotation, "highlighted_text"),
                "id": _safe_string(annotation, "annotation_id"),
                "location": {},
                "type": "note",
                "noteText": _safe_string(annotation, "note_text"),
            }))
            reasons.append(_failure_reason("emergency_row_render", error))
    body = b'{"annotations":[' + b",".join(objects) \
        + b'],"nextPageOffsetToken":null}'
    digest = hashlib.sha256(body).hexdigest()
    state, normalized_content_id, state_reason = _book_state(
        user_id, book_id, entitlement_id,
    )
    if state_reason is not None:
        reasons.append(state_reason)
    if state is None:
        etag = _transient_etag(
            user_id, book_id, normalized_content_id, digest,
        )
    else:
        try:
            etag = _commit_complete_render(
                state=state, body=body, digest=digest,
                annotation_count=len(rows),
            )
        except Exception as error:
            _safe_rollback()
            reasons.append(_failure_reason("book_state_commit", error))
            # The complete live body is newer evidence than any stored
            # snapshot. Persistence failure may degrade durability, but must
            # never replace known-complete live membership with an older set.
            etag = _transient_etag(
                user_id, book_id, normalized_content_id, digest,
            )
    _log_degraded(
        log, user_id=user_id, book_id=book_id,
        visible_count=len(rows), reasons=reasons,
    )
    return body, etag


def _render_owned_annotations(*, user_id, book_id, entitlement_id, page_limit,
                              device_id, log):
    objects = []
    reasons = []
    rows = _annotation_rows(user_id, book_id, page_limit)
    if len(rows) > page_limit:
        return None
    if not _render_count_is_safe(
        user_id=user_id, book_id=book_id, device_id=device_id,
        row_count=len(rows),
        row_ids={annotation.annotation_id for annotation, _ in rows},
    ):
        return None
    for annotation, materialization in rows:
        try:
            raw = _exact_raw(annotation, materialization)
            if raw is not None:
                objects.append(raw)
                continue
            mapped, faithful, reason = _fallback_object(annotation, entitlement_id)
            if not faithful:
                reasons.append(f"column_fallback:{reason}")
            objects.append(_encode_object(mapped))
        except Exception as error:
            # A single malformed row or serializer bug cannot abort a
            # replacement-set GET.  Preserve at least its id and user text.
            objects.append(_encode_object(_emergency_object(annotation, entitlement_id)))
            reasons.append(_failure_reason("row_render", error))

    body = b'{"annotations":[' + b",".join(objects) \
        + b'],"nextPageOffsetToken":null}'
    digest = hashlib.sha256(body).hexdigest()
    state, normalized_content_id, state_reason = _book_state(
        user_id, book_id, entitlement_id,
    )
    if state_reason is not None:
        reasons.append(state_reason)

    if state is None:
        etag = _transient_etag(
            user_id, book_id, normalized_content_id, digest,
        )
    else:
        try:
            etag = _commit_complete_render(
                state=state, body=body, digest=digest,
                annotation_count=len(rows),
            )
        except Exception as error:
            _safe_rollback()
            reasons.append(_failure_reason("book_state_commit", error))
            # Never discard the current complete render in favor of a prior
            # snapshot merely because its persistence/ETag commit failed.
            etag = _transient_etag(
                user_id, book_id, normalized_content_id, digest,
            )

    _log_degraded(
        log, user_id=user_id, book_id=book_id,
        visible_count=len(rows), reasons=reasons,
    )
    return body, etag


def render_owned_annotations(*, user_id, book_id, entitlement_id, page_limit,
                             device_id, log):
    """Return a complete local page, or ``None`` when the set is too large."""
    try:
        return _render_owned_annotations(
            user_id=user_id,
            book_id=book_id,
            entitlement_id=entitlement_id,
            page_limit=page_limit,
            device_id=device_id,
            log=log,
        )
    except Exception as error:
        # A failed joined query may leave SQLAlchemy's transaction unusable.
        # Reset that read transaction before retrying the simpler row query;
        # otherwise the emergency path could unnecessarily lose every row.
        _safe_rollback()
        return _emergency_render(
            user_id=user_id,
            book_id=book_id,
            entitlement_id=entitlement_id,
            page_limit=page_limit,
            device_id=device_id,
            log=log,
            reason=_failure_reason("render", error),
        )


def render_authoritative_complete_set(*, user_id, book_id, entitlement_id,
                                      page_limit, device_id, log, reason):
    """Rebuild or replay known authority; ``None`` requests terminal 503."""
    return _emergency_render(
        user_id=user_id,
        book_id=book_id,
        entitlement_id=entitlement_id,
        page_limit=page_limit,
        device_id=device_id,
        log=log,
        reason=reason,
        force_authoritative=True,
    )
