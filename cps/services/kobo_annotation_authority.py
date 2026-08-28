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

An owned annotations GET must also never become an error response.  Nickel has
been observed treating an error as an empty replacement set, so a degraded 200
containing every row's identity and text is safer than a 5xx that is perfectly
honest about a rendering or state-persistence failure.  Book-state persistence
is consequently advisory, and row rendering has a last-resort wire mapping.

This renderer is used only when the complete current set fits in the requested
page.  The route proxies larger sets to Kobo until CWNG implements the immutable
snapshot + cursor contract; a local response can therefore honestly terminate
with ``nextPageOffsetToken`` set to null.
"""

from __future__ import annotations

import hashlib
import json
import math
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


def local_get_is_eligible(*, settings, user, book_id, entitlement_id, page_limit, log):
    """Fail closed unless CWNG can return one complete, authoritative page."""
    try:
        if not isinstance(page_limit, int) or isinstance(page_limit, bool):
            return False
        if page_limit < 1 or page_limit > 100:
            return False

        from cps.services import kobo_annotation_stage0

        schema_ready = kobo_annotation_stage0.schema_capable(ub.session.get_bind())
        state = _state_for_book(user.id, book_id)
        if not kobo_annotation_stage0.gates_allow(
            settings, user, state, schema_ready=schema_ready,
        ):
            return False
        if _normalized_entitlement_id(state.content_id) != _normalized_entitlement_id(
            entitlement_id,
        ):
            return False

        # Cursor snapshots are deliberately outside this branch. Read at most
        # one id beyond the requested page so an over-limit set fails closed to
        # Kobo without loading or rendering the complete collection locally.
        visible_ids = (
            ub.session.query(ub.Annotation.id)
            .filter(
                ub.Annotation.user_id == user.id,
                ub.Annotation.book_id == book_id,
                (
                    ub.Annotation.hidden.is_(None)
                    | (ub.Annotation.hidden == False)  # noqa: E712
                ),
            )
            .limit(page_limit + 1)
            .all()
        )
        return len(visible_ids) <= page_limit
    except Exception:
        _safe_rollback()
        try:
            log.warning(
                "Owned Kobo annotation GET eligibility failed; proxying "
                "user_id=%s book_id=%s",
                getattr(user, "id", None), book_id, exc_info=True,
            )
        except Exception:
            pass
        return False


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


def _emergency_render(*, user_id, book_id, entitlement_id, page_limit, log, reason):
    rows, query_reason = _emergency_rows(user_id, book_id, page_limit)
    if len(rows) > page_limit:
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
    normalized_content_id = _normalized_entitlement_id(entitlement_id)
    etag = _transient_etag(
        user_id, book_id, normalized_content_id, digest,
    )
    _log_degraded(
        log, user_id=user_id, book_id=book_id,
        visible_count=len(rows), reasons=reasons,
    )
    return body, etag


def _render_owned_annotations(*, user_id, book_id, entitlement_id, page_limit, log):
    objects = []
    reasons = []
    rows = _annotation_rows(user_id, book_id, page_limit)
    if len(rows) > page_limit:
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
            # Commit directly so this boundary owns the one sanitized error
            # log. ``ub.session_commit`` logs before returning False, which
            # would duplicate the degraded-response record below.
            ub.session.commit()
        except Exception as error:
            _safe_rollback()
            reasons.append(_failure_reason("book_state_commit", error))
            etag = _transient_etag(
                user_id, book_id, normalized_content_id, digest,
            )

    _log_degraded(
        log, user_id=user_id, book_id=book_id,
        visible_count=len(rows), reasons=reasons,
    )
    return body, etag


def render_owned_annotations(*, user_id, book_id, entitlement_id, page_limit, log):
    """Return a complete local page, or ``None`` when the set is too large."""
    try:
        return _render_owned_annotations(
            user_id=user_id,
            book_id=book_id,
            entitlement_id=entitlement_id,
            page_limit=page_limit,
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
            log=log,
            reason=_failure_reason("render", error),
        )
