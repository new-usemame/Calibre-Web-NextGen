#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""KOReader annotation bridge — device-agnostic pull/push API (Phase 2).

Two routes on the existing ``kosync`` blueprint, reusing its auth + book
resolution verbatim (no new credentials for users):

    GET /kosync/syncs/annotations/<document>  -> pull (server -> device)
    PUT /kosync/syncs/annotations             -> push (device -> server)

``<document>`` is the KOReader partial-MD5 digest, resolved to a calibre book
via ``get_book_by_checksum`` exactly as progress sync does, so annotations
converge on the same book across formats/checksums.

The wire shape is the portable annotation dict (see
``cps/services/annotation_portable.py``); the plugin's device provider maps it
to device-native fields (KoboReader.sqlite). Pull includes ``hidden`` rows so
the device can delete locally; push records ``device_origin_id`` to suppress
feedback loops and fans out to enabled sync targets (Hardcover).

The route handlers are thin; ``build_pull_payload`` + ``apply_push`` hold the
testable logic. See notes/2026-05-25-annotation-two-way-phase1-phase2-DESIGN.md §4.
"""

from __future__ import annotations

from datetime import datetime, timezone

from flask import request

from ... import csrf, logger, ub
from .kosync import (
    kosync,
    authenticate_user,
    get_book_by_checksum,
    create_sync_response,
    is_valid_key_field,
    _require_kosync_enabled,
    ERROR_UNAUTHORIZED_USER,
    ERROR_DOCUMENT_FIELD_MISSING,
)

log = logger.create()

# Sources a push may declare itself complete for. A device may only reconcile
# the source it actually owns, so an unknown/spoofed value reaps nothing.
_REAPABLE_SOURCES = {"koreader"}


def _now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Testable core
# ---------------------------------------------------------------------------


def build_pull_payload(user_id: int, book_id: int, session) -> dict:
    """Portable annotations for one user + book, INCLUDING hidden rows so the
    device can mirror deletions locally."""
    from ...services.annotation_portable import to_portable
    rows = (
        session.query(ub.Annotation)
        .filter(ub.Annotation.user_id == user_id, ub.Annotation.book_id == book_id)
        .order_by(ub.Annotation.id.asc())
        .all()
    )
    annotations = [to_portable(r) for r in rows]
    return {"annotations": annotations, "annotation_count": len(annotations)}


def apply_push(annotations, *, user, book, session, commit,
               complete_for_source=None) -> dict:
    """Upsert each pushed portable annotation, fan out to enabled sync targets,
    and return a counts summary keyed by action (created/updated/deleted/skipped).

    ``complete_for_source`` makes the push authoritative for one source: the
    caller asserts ``annotations`` is the device's COMPLETE live set for this
    book, so any live row of that source which is absent has been deleted on
    the device and is reaped (see :func:`_reap_absent`). Left ``None``, the
    push is treated as partial and nothing is reaped.
    """
    from ...services.annotation_portable import apply_portable
    from ...services import annotation_sync

    summary = {"created": 0, "updated": 0, "deleted": 0, "skipped": 0}
    if not isinstance(annotations, list):
        return summary
    for payload in annotations:
        row, action = apply_portable(
            payload, user_id=user.id, book=book, session=session, commit=commit,
        )
        summary[action] = summary.get(action, 0) + 1
        if row is None or action == "skipped":
            continue
        try:
            if action == "deleted":
                annotation_sync.dispatch_annotation_deletes(
                    [row.annotation_id], user, book_id=book.id,
                )
            else:
                annotation_sync.dispatch_existing_annotation_sync(row, book, user)
        except Exception:  # pragma: no cover - fan-out must never fail the push
            log.exception("koreader annotation push fan-out failed for %s", row.annotation_id)

    if complete_for_source:
        summary["deleted"] += _reap_absent(
            annotations, user=user, book=book, session=session, commit=commit,
            source=complete_for_source,
        )
    return summary


def _reap_absent(annotations, *, user, book, session, commit, source) -> int:
    """Soft-delete rows the device no longer has.

    KOReader leaves no tombstone when a highlight is deleted — the entry just
    disappears from its annotation collection — so a device-side delete reaches
    us as an omission from a complete push. Reconciling the pushed set against
    the stored one is the only way to observe it (#905).

    Scoped hard, because reaping is destructive-in-effect:
      - only this ``(user, book)``;
      - only rows of ``source`` (a KOReader sync must never reap a Kobo-native
        or web-reader highlight — those devices push their own complete sets);
      - only rows that are still live (an already-hidden row is left alone, so
        the delete fan-out fires once, not on every subsequent sync).

    Soft-deletes rather than deleting: pull deliberately includes hidden rows so
    other devices can mirror the deletion locally.
    """
    from ...services import annotation_sync

    pushed_ids = {
        payload.get("annotation_id").strip()
        for payload in annotations
        if isinstance(payload, dict)
        and isinstance(payload.get("annotation_id"), str)
        and payload.get("annotation_id").strip()
    }

    stale = [
        row for row in session.query(ub.Annotation).filter(
            ub.Annotation.user_id == user.id,
            ub.Annotation.book_id == book.id,
            ub.Annotation.source == source,
        ).filter(
            (ub.Annotation.hidden.is_(None))
            | (ub.Annotation.hidden == False)  # noqa: E712 — SQLA needs ==
        ).all()
        if row.annotation_id not in pushed_ids
    ]
    if not stale:
        return 0

    for row in stale:
        row.hidden = True
        row.last_synced = _now()
    commit()

    for row in stale:
        try:
            annotation_sync.dispatch_annotation_deletes(
                [row.annotation_id], user, book_id=book.id,
            )
        except Exception:  # pragma: no cover - fan-out must never fail the push
            log.exception("koreader annotation reap fan-out failed for %s", row.annotation_id)
    log.debug(
        "koreader reap: soft-deleted %d absent %s row(s) for user=%s book=%s",
        len(stale), source, user.id, book.id,
    )
    return len(stale)


# ---------------------------------------------------------------------------
# Routes (thin; reuse kosync auth + book resolution)
# ---------------------------------------------------------------------------


@csrf.exempt
@kosync.route("/kosync/syncs/annotations/<document>", methods=["GET"])
def pull_annotations(document: str):
    """Pull annotations for the book the digest resolves to (server -> device)."""
    blocked = _require_kosync_enabled()
    if blocked:
        return blocked
    user = authenticate_user()
    if not user:
        return create_sync_response({"error": ERROR_UNAUTHORIZED_USER, "message": "Unauthorized"}, 401)
    if not is_valid_key_field(document):
        return create_sync_response({"error": ERROR_DOCUMENT_FIELD_MISSING, "message": "Invalid document field"}, 400)

    book_id, _fmt, _title, _path, _ver = get_book_by_checksum(document)
    if not book_id:
        # Unknown book: empty set, not an error (the device may have a book the
        # server doesn't know yet).
        return create_sync_response({"document": document, "annotations": [], "annotation_count": 0})

    payload = build_pull_payload(user.id, book_id, ub.session)
    payload["document"] = document
    payload["calibre_book_id"] = book_id
    return create_sync_response(payload)


@csrf.exempt
@kosync.route("/kosync/syncs/annotations", methods=["PUT"])
def push_annotations():
    """Accept device-created/changed/deleted annotations (device -> server)."""
    blocked = _require_kosync_enabled()
    if blocked:
        return blocked
    user = authenticate_user()
    if not user:
        return create_sync_response({"error": ERROR_UNAUTHORIZED_USER, "message": "Unauthorized"}, 401)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return create_sync_response({"error": "invalid_payload", "message": "JSON object required"}, 400)
    document = data.get("document")
    if not is_valid_key_field(document):
        return create_sync_response({"error": ERROR_DOCUMENT_FIELD_MISSING, "message": "Invalid document field"}, 400)

    book_id, _fmt, _title, _path, _ver = get_book_by_checksum(document)
    if not book_id:
        return create_sync_response({"document": document, "matched": False,
                                     "created": 0, "updated": 0, "deleted": 0, "skipped": 0})

    from ... import calibre_db
    book = calibre_db.get_book(book_id)
    if book is None:
        return create_sync_response({"document": document, "matched": False,
                                     "created": 0, "updated": 0, "deleted": 0, "skipped": 0})

    # A plugin that pushes its complete local set says so here, which lets the
    # server observe device-side deletions (they arrive as omissions, never as
    # tombstones — #905). Absent/false keeps the legacy partial-push semantics,
    # so an older plugin build reaps nothing.
    complete_for_source = None
    if data.get("complete") is True:
        claimed = data.get("complete_source") or "koreader"
        if claimed in _REAPABLE_SOURCES:
            complete_for_source = claimed
        else:
            log.warning("koreader push declared complete for unsupported source %r; not reaping", claimed)

    annotations = data.get("annotations")
    # Lua has no empty-list/empty-object distinction, so the plugin's JSON
    # encoder emits `{}` for "no annotations". A complete push is the one case
    # where an empty payload is meaningful (the user deleted their last
    # highlight), so accept that exact shape there.
    #
    # `{}` ONLY — a null or missing `annotations` is a malformed request, not an
    # assertion that the device has none, and reading it as an empty
    # authoritative set would reap the whole book. That's unrecoverable: a
    # tombstoned row is deliberately never un-hidden by a later push
    # (apply_portable preserves tombstones), so the highlights would not come
    # back even though the device still has them. Malformed still 400s.
    if complete_for_source and annotations == {}:
        annotations = []
    if not isinstance(annotations, list):
        return create_sync_response({"error": "invalid_annotations", "message": "annotations must be an array"}, 400)
    from ...services.annotation_portable import validate_portable_payload
    for index, payload in enumerate(annotations):
        error = validate_portable_payload(payload)
        if error:
            return create_sync_response({
                "error": "invalid_annotation",
                "message": f"annotations[{index}]: {error}",
            }, 400)

    summary = apply_push(
        annotations, user=user, book=book,
        session=ub.session, commit=ub.session_commit,
        complete_for_source=complete_for_source,
    )
    summary["document"] = document
    summary["reconciled"] = complete_for_source is not None
    summary["calibre_book_id"] = book_id
    summary["matched"] = True
    return create_sync_response(summary)
