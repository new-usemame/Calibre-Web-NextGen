# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render CWNG's complete owned-book annotation set for Kobo Nickel.

Owned annotation GETs are replacement-set operations on the device: a partial,
empty-on-error, or non-success response can destroy Nickel's local rows.  This
module therefore has one non-negotiable invariant: every visible database row
is represented in the returned array.  Exact Kobo materializations win.  When
one is absent or stale, the generic columns are mapped conservatively; an
imperfect mapping is logged and included, never silently omitted.

Pagination is deliberately unnecessary here.  The complete current set is
returned in one response (therefore at least Nickel's measured ``limit=100``),
and ``nextPageOffsetToken`` is always null.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timezone

from cps import ub
from cps.services.annotation_colors import KOBO_BOOKMARK_COLOR_HEX, to_storage_color
from cps.services.annotation_types import to_storage_type
from cps.services.kobo_annotation_capture import project_exact_materialization


_KOBO_WIRE_COLORS = frozenset(KOBO_BOOKMARK_COLOR_HEX.values())
_EPOCH = "1970-01-01T00:00:00.000Z"


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


def _annotation_rows(user_id, book_id):
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
        .all()
    )


def _book_state(user_id, book_id, entitlement_id):
    state = (
        ub.session.query(ub.KoboAnnotationBookState)
        .filter(
            ub.KoboAnnotationBookState.user_id == user_id,
            ub.KoboAnnotationBookState.book_id == book_id,
        )
        .first()
    )
    if state is None:
        state = ub.KoboAnnotationBookState(
            user_id=user_id,
            book_id=book_id,
            content_id=entitlement_id,
            authority_status="unseeded",
            authority_revision=0,
            generation_id=str(uuid.uuid4()),
            opaque_content_status="unknown",
        )
        ub.session.add(state)
        ub.session.flush()
    try:
        if str(uuid.UUID(state.generation_id)) != state.generation_id:
            raise ValueError("non-canonical generation id")
    except (ValueError, TypeError, AttributeError):
        state.generation_id = str(uuid.uuid4())
    return state


def render_owned_annotations(*, user_id, book_id, entitlement_id, log):
    """Return the complete envelope bytes and its CWNG revision ETag."""
    objects = []
    imperfect = []
    for annotation, materialization in _annotation_rows(user_id, book_id):
        raw = _exact_raw(annotation, materialization)
        if raw is not None:
            objects.append(raw)
            continue
        mapped, faithful, reason = _fallback_object(annotation, entitlement_id)
        if not faithful:
            imperfect.append((annotation.annotation_id, reason))
        objects.append(json.dumps(
            mapped, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8"))

    if imperfect:
        log.error(
            "Owned Kobo annotation GET included %d loss-preserving column "
            "fallback(s) user_id=%s book_id=%s reasons=%s",
            len(imperfect), user_id, book_id,
            [(annotation_id, reason) for annotation_id, reason in imperfect],
        )

    body = b'{"annotations":[' + b",".join(objects) \
        + b'],"nextPageOffsetToken":null}'
    digest = hashlib.sha256(body).hexdigest()
    state = _book_state(user_id, book_id, entitlement_id)
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
    if ub.session_commit() is False:
        # The device's replacement-set safety is more important than caching.
        # Serve the complete set with this request's computed token even when
        # persisting the token failed; the next GET will still receive 200.
        log.error(
            "Could not persist owned Kobo annotation ETag user_id=%s book_id=%s",
            user_id, book_id,
        )
    return body, etag
