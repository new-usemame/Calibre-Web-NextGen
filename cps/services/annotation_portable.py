# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Device-agnostic portable annotation projection (Phase 2).

The KOReader-bridge endpoints speak a portable annotation shape that is
independent of any device kind; the plugin's device provider maps it to
device-native fields (KoboReader.sqlite Bookmark columns, etc.).

  - :func:`to_portable` — project an Annotation ORM row to the wire dict
    (pull: server → device).
  - :func:`apply_portable` — upsert an Annotation from a pushed wire dict
    (push: device → server), recording ``device_origin_id`` for feedback-loop
    suppression and soft-deleting on ``hidden`` only when the calling protocol
    authorizes the stored provenance.

Kept dependency-light + explicit so it's unit-testable without a Flask
request context (mirrors cps/annotations.py's pure helpers).

See notes/2026-05-25-annotation-two-way-phase1-phase2-DESIGN.md §4.1.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Collection, Optional, Tuple

from sqlalchemy import exc

from .. import logger
from .annotation_colors import to_display_name, to_storage_color
from .annotation_types import to_storage_type

log = logger.create()

_VALID_SOURCES = {"kobo", "webreader", "koreader"}


def validate_portable_payload(payload, *, book_uuid=None) -> Optional[str]:
    """Return a validation error for fields that would make an upsert unsafe."""
    if not isinstance(payload, dict):
        return None  # non-object entries are deliberately counted as skipped
    if "source" in payload and payload.get("source") not in _VALID_SOURCES:
        return "source must be one of: " + ", ".join(sorted(_VALID_SOURCES))
    for field in ("annotation_id", "highlighted_text", "note_text", "color",
                  "content_id", "context_string", "position_type",
                  "start_xpointer", "end_xpointer", "device_origin_id"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            return f"{field} must be a string or null"
    for field in ("start_kobospan", "end_kobospan"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            return f"{field} must be a string or null"
    for field in ("start_offset", "end_offset"):
        value = payload.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            return f"{field} must be an integer or null"
    if "hidden" in payload and not isinstance(payload.get("hidden"), bool):
        return "hidden must be a boolean"
    if "content_id" in payload and payload.get("content_id") is not None:
        from .annotation_content_id import ContentIdError, normalize_content_id
        try:
            normalize_content_id(payload.get("content_id"), book_uuid=book_uuid)
        except ContentIdError as error:
            return f"content_id: {error}"
    return None


def _now():
    return datetime.now(timezone.utc)


def to_portable(row) -> dict:
    """Project an Annotation row to the portable wire dict."""
    from .kobo_position import _extract_kobospan_id
    return {
        "annotation_id": row.annotation_id,
        "highlighted_text": row.highlighted_text,
        "note_text": row.note_text,
        # The portable wire speaks display names (the KOReader plugin's own
        # provider builds and consumes names); the column speaks canonical hex.
        # Normalise on the way out so a device never sees "#A0A0A0". A colour
        # this app cannot name is passed through as the stored token rather
        # than dropped — the receiving provider is then the one deciding what
        # to do with a word it does not know, which is better than us deleting
        # the user's colour on its behalf.
        "color": to_display_name(row.highlight_color),
        "content_id": row.content_id,
        "start_kobospan": _extract_kobospan_id(row.start_container_path or ""),
        "start_offset": row.start_offset,
        "end_kobospan": _extract_kobospan_id(row.end_container_path or ""),
        "end_offset": row.end_offset,
        "context_string": row.context_string,
        "chapter_progress": row.chapter_progress,
        "position_type": row.position_type,
        "start_xpointer": row.start_xpointer,
        "end_xpointer": row.end_xpointer,
        # Additive since F-9de049: without it a KOReader export -> import round
        # trip silently dropped the type, because neither side ever mentioned it.
        # A receiver that does not understand the key ignores it.
        "type": row.annotation_type,
        "source": row.source,
        "hidden": bool(row.hidden),
        "device_origin_id": row.device_origin_id,
        "last_synced": row.last_synced.isoformat() if row.last_synced else None,
    }


def apply_portable(payload, *, user_id, book, session, commit,
                   origin_device_id=None,
                   deletable_sources: Collection[str] = (),
                   ) -> Tuple[Optional[object], str]:
    """Upsert an Annotation from a device-pushed portable dict.

    Find-or-create keyed on ``(user_id, book_id, annotation_id)``. New rows take
    a recognized payload ``source`` and default a missing source to ``koreader``;
    existing rows retain their stored provenance. ``deletable_sources`` is the
    calling protocol's authority for soft-deleting an existing row; the helper
    defaults to allowing none. Position fields are built from the KoboSpan
    anchors like the web-reader create path.
    ``device_origin_id`` is recorded so the next pull won't echo the row back to
    the device. ``hidden: true`` soft-deletes.

    Returns ``(row, action)`` where action ∈ {created, updated, deleted, skipped}.
    """
    from cps import ub

    if not isinstance(payload, dict):
        return None, "skipped"
    annotation_id = payload.get("annotation_id")
    if not isinstance(annotation_id, str) or not annotation_id.strip():
        return None, "skipped"
    annotation_id = annotation_id.strip()
    source = payload.get("source", "koreader")
    if source not in _VALID_SOURCES:
        # The HTTP boundary rejects this with a reason. Keep the core helper
        # defensive as well: coercing an unrecognised observation to
        # ``koreader`` invents provenance and grants KOReader delete authority
        # over a row whose origin it did not establish.
        return None, "skipped"

    row = (
        session.query(ub.Annotation)
        .filter(ub.Annotation.user_id == user_id,
                ub.Annotation.book_id == book.id,
                ub.Annotation.annotation_id == annotation_id)
        .first()
    )
    created = False
    if row is None:
        row = ub.Annotation(
            user_id=user_id, annotation_id=annotation_id,
            book_id=book.id, source=source, origin_device_id=origin_device_id,
            # Preserved, never chosen: a sender that omits it leaves NULL rather
            # than being assigned a type this side invented (F-9de049).
            annotation_type=to_storage_type(payload.get("type")),
        )
        session.add(row)
        created = True
    else:
        if "source" in payload and payload.get("source") != row.source:
            # Keep the 200 response for compatibility, but never let a refused
            # authority claim disappear without the details needed to diagnose
            # why the device's requested change did not land.
            log.warning(
                "Portable annotation push ignored source change: user=%s book=%s "
                "annotation_id=%r stored_source=%r claimed_source=%r",
                user_id, getattr(book, "id", "?"), annotation_id,
                row.source, payload.get("source"),
            )
        if row.hidden and not payload.get("hidden"):
            # A complete-list retry has no mutation clock, so it cannot prove an
            # intentional recreation. Preserve the tombstone and every stored field.
            return row, "skipped"
    # ``source`` is provenance, not editable content. Every legitimate origin
    # is assigned at creation by its ingest route (Kobo PATCH, web reader, the
    # KoboReader.sqlite import, or this KOReader bridge). No ordinary annotation
    # push carries authority to migrate an existing row between those origins;
    # such a migration would need its own operator-gated operation. In
    # particular, accepting a claimed ``koreader`` source here would let the
    # next named-delete push bypass _DELETABLE_SOURCES and erase a Kobo/web-
    # reader row (F-1927e0).

    hidden_requested = bool(payload.get("hidden"))
    hidden_permitted = created or row.source in deletable_sources
    if hidden_requested and not hidden_permitted and not bool(row.hidden):
        log.warning(
            "Portable annotation push refused hidden: user=%s book=%s "
            "annotation_id=%r stored_source=%r deletable_sources=%s",
            user_id, getattr(book, "id", "?"), annotation_id, row.source,
            sorted(deletable_sources),
        )

    before = None if created else (
        row.source, row.highlighted_text, row.note_text, row.highlight_color,
        row.content_id, row.context_string, row.chapter_progress,
        row.position_type, row.start_xpointer, row.end_xpointer,
        row.start_container_path, row.start_offset,
        row.end_container_path, row.end_offset,
        row.device_origin_id, bool(row.hidden),
    )

    # Content fields (only overwrite when present in the payload).
    if "highlighted_text" in payload:
        row.highlighted_text = payload.get("highlighted_text")
    if "note_text" in payload:
        row.note_text = payload.get("note_text")
    if "color" in payload:
        # Accepts a name (what KOReader and older backups send) or a hex, and
        # stores the canonical form. A legacy-name row rewritten to its hex
        # counts as an update on the first push after this change; that is the
        # normalisation landing, not a content change.
        row.highlight_color = to_storage_color(payload.get("color"))
    if payload.get("content_id"):
        from .annotation_content_id import normalize_content_id
        row.content_id = normalize_content_id(
            payload.get("content_id"), book_uuid=getattr(book, "uuid", None),
        )
    if payload.get("context_string"):
        row.context_string = payload.get("context_string")
    if payload.get("chapter_progress") is not None:
        row.chapter_progress = payload.get("chapter_progress")

    if payload.get("position_type") == "koreader_xpointer":
        start_xpointer = payload.get("start_xpointer")
        end_xpointer = payload.get("end_xpointer")
        if isinstance(start_xpointer, str) and start_xpointer:
            row.position_type = "koreader_xpointer"
            row.start_xpointer = start_xpointer
            row.end_xpointer = end_xpointer if isinstance(end_xpointer, str) else None

    # Position — build the Kobo-native selector form from the KoboSpan anchor.
    start_span = payload.get("start_kobospan")
    if start_span:
        end_span = payload.get("end_kobospan") or start_span
        row.start_container_path = "span#" + start_span
        row.start_container_child_index = -99
        row.start_offset = int(payload.get("start_offset") or 0)
        row.end_container_path = "span#" + end_span
        row.end_container_child_index = -99
        row.end_offset = int(payload.get("end_offset") or 0)

    if payload.get("device_origin_id"):
        row.device_origin_id = payload.get("device_origin_id")

    if hidden_requested and hidden_permitted:
        row.hidden = True
        action = "deleted"
    elif hidden_requested:
        # Preserve the stored state. If no permitted content field changed,
        # the before/after check below reports this as ``skipped`` and the
        # protocol performs no fan-out of any kind.
        action = "updated"
    else:
        row.hidden = False
        action = "created" if created else "updated"

    after = (
        row.source, row.highlighted_text, row.note_text, row.highlight_color,
        row.content_id, row.context_string, row.chapter_progress,
        row.position_type, row.start_xpointer, row.end_xpointer,
        row.start_container_path, row.start_offset,
        row.end_container_path, row.end_offset,
        row.device_origin_id, bool(row.hidden),
    )
    if not created and before == after:
        return row, "skipped"

    row.last_synced = _now()
    try:
        commit()
    except exc.IntegrityError:
        # A parallel device may have inserted the same canonical identity
        # after our SELECT. Roll back this losing INSERT and replay as an
        # update; the unique index is the serialization point.
        session.rollback()
        winner = (
            session.query(ub.Annotation)
            .filter(ub.Annotation.user_id == user_id,
                    ub.Annotation.book_id == book.id,
                    ub.Annotation.annotation_id == annotation_id)
            .one_or_none()
        )
        if winner is None:
            raise
        return apply_portable(
            payload, user_id=user_id, book=book, session=session, commit=commit,
            origin_device_id=origin_device_id,
            deletable_sources=deletable_sources,
        )
    return row, action
