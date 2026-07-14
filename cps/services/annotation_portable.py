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
    suppression and soft-deleting on ``hidden``.

Kept dependency-light + explicit so it's unit-testable without a Flask
request context (mirrors cps/annotations.py's pure helpers).

See notes/2026-05-25-annotation-two-way-phase1-phase2-DESIGN.md §4.1.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import exc

_VALID_SOURCES = {"kobo", "webreader", "koreader"}


def validate_portable_payload(payload) -> Optional[str]:
    """Return a validation error for fields that would make an upsert unsafe."""
    if not isinstance(payload, dict):
        return None  # non-object entries are deliberately counted as skipped
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
        "color": row.highlight_color,
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
        "source": row.source,
        "hidden": bool(row.hidden),
        "device_origin_id": row.device_origin_id,
        "last_synced": row.last_synced.isoformat() if row.last_synced else None,
    }


def apply_portable(payload, *, user_id, book, session, commit) -> Tuple[Optional[object], str]:
    """Upsert an Annotation from a device-pushed portable dict.

    Find-or-create keyed on ``(user_id, book_id, annotation_id)``. New rows take the
    payload's ``source`` (coerced to ``koreader`` if absent/invalid). Position
    fields are built from the KoboSpan anchors like the web-reader create path.
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

    row = (
        session.query(ub.Annotation)
        .filter(ub.Annotation.user_id == user_id,
                ub.Annotation.book_id == book.id,
                ub.Annotation.annotation_id == annotation_id)
        .first()
    )
    created = False
    if row is None:
        source = payload.get("source")
        if source not in _VALID_SOURCES:
            source = "koreader"
        row = ub.Annotation(
            user_id=user_id, annotation_id=annotation_id,
            book_id=book.id, source=source,
        )
        session.add(row)
        created = True
    elif row.hidden and not payload.get("hidden"):
        # A complete-list retry has no mutation clock, so it cannot prove an
        # intentional recreation. Preserve the tombstone and every stored field.
        return row, "skipped"
    elif payload.get("source") in _VALID_SOURCES:
        row.source = payload.get("source")

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
        row.highlight_color = payload.get("color")
    if payload.get("content_id"):
        row.content_id = payload.get("content_id")
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

    if payload.get("hidden"):
        row.hidden = True
        action = "deleted"
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
        )
    return row, action
