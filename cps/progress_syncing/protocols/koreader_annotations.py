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

Deletions are NAMED by the device (``deleted: [annotation_id, ...]``), never
inferred from what a push omits. #906 tried the inference — a push could declare
itself ``complete`` and the server reaped every live row it omitted — but these
two pushes are byte-identical on the wire:

    the user deleted their last highlight   (#905, must delete)
    this device never had those highlights  (#920, must not delete)

and the KOReader-native provider is push-only (``applyToDevice`` is a no-op off
Kobo), so a second device could never receive the first device's highlights yet
still declared its empty set complete — silently destroying them, permanently,
since ``apply_portable`` never un-hides a tombstone. Only the device can tell
the two apart, because only it knows what it used to have, so the decision lives
there and the server obeys. ``complete`` is still accepted and ignored.

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

# Sources a push may delete from. A device may only delete rows of the source it
# actually owns, so a KOReader sync can never touch a Kobo-native or web-reader
# highlight.
_DELETABLE_SOURCES = {"koreader"}


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
               deleted_ids=None, delete_source="koreader") -> dict:
    """Upsert each pushed portable annotation, fan out to enabled sync targets,
    and return a counts summary keyed by action (created/updated/deleted/skipped).

    ``deleted_ids`` names the annotations the device knows it used to have and
    the user has since deleted; they are soft-deleted (see
    :func:`_apply_deletes`). Omission from ``annotations`` means nothing on its
    own — see the module docstring for why the server never infers a delete.
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

    if deleted_ids:
        summary["deleted"] += _apply_deletes(
            deleted_ids, user=user, book=book, session=session, commit=commit,
            source=delete_source,
        )
    return summary


def _apply_deletes(deleted_ids, *, user, book, session, commit, source) -> int:
    """Soft-delete the rows the device reported as deleted.

    KOReader leaves no tombstone when a highlight is deleted — the entry just
    disappears from its annotation collection — so the plugin reconstructs the
    deletion by diffing its live set against the watermark of what it last
    pushed, and names the missing ids here (#905).

    Scoped hard, because deleting is destructive-in-effect:
      - only this ``(user, book)``;
      - only rows of ``source`` (a KOReader sync must never delete a
        Kobo-native or web-reader highlight — those devices own their own);
      - only rows that are still live (an already-hidden row is left alone, so
        the delete fan-out fires once, not on every subsequent sync).

    Soft-deletes rather than deleting: pull deliberately includes hidden rows so
    other devices can mirror the deletion locally.
    """
    from ...services import annotation_sync

    wanted = {
        aid.strip() for aid in deleted_ids
        if isinstance(aid, str) and aid.strip()
    }
    if not wanted:
        return 0

    stale = [
        row for row in session.query(ub.Annotation).filter(
            ub.Annotation.user_id == user.id,
            ub.Annotation.book_id == book.id,
            ub.Annotation.source == source,
        ).filter(
            (ub.Annotation.hidden.is_(None))
            | (ub.Annotation.hidden == False)  # noqa: E712 — SQLA needs ==
        ).all()
        if row.annotation_id in wanted
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
            log.exception("koreader annotation delete fan-out failed for %s", row.annotation_id)
    log.debug(
        "koreader delete: soft-deleted %d reported %s row(s) for user=%s book=%s",
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

    # Deletions are named, never inferred. `complete` (#906) is accepted and
    # ignored: it asked the server to reap every live row the push omitted, but
    # "the user deleted these" and "this device never had these" are the same
    # push on the wire, so the server cannot tell them apart and a push-only
    # device destroyed the other devices' highlights (#920). Only the device
    # knows which it means, so only the device may say.
    # Lua has no empty-list/empty-object distinction, so the plugin's JSON
    # encoder emits `{}` for an empty table. Normalise both fields, which is
    # safe now that an empty set asserts nothing. A null/missing `annotations`
    # stays malformed.
    annotations = data.get("annotations")
    if annotations == {}:
        annotations = []
    if not isinstance(annotations, list):
        return create_sync_response({"error": "invalid_annotations", "message": "annotations must be an array"}, 400)

    deleted_ids = data.get("deleted")
    if deleted_ids == {} or deleted_ids is None:
        deleted_ids = []
    if not isinstance(deleted_ids, list) or any(
        not isinstance(aid, str) or not aid.strip() for aid in deleted_ids
    ):
        return create_sync_response({
            "error": "invalid_deleted",
            "message": "deleted must be an array of annotation_id strings",
        }, 400)

    # Only meaningful when something is being deleted. Rejecting it on a push
    # that deletes nothing would throw away the annotations that push carries
    # over a field with no effect.
    delete_source = data.get("delete_source", "koreader")
    if deleted_ids and (
        not isinstance(delete_source, str) or delete_source not in _DELETABLE_SOURCES
    ):
        return create_sync_response({
            "error": "invalid_delete_source",
            "message": "delete_source must be one of: %s" % ", ".join(sorted(_DELETABLE_SOURCES)),
        }, 400)
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
        deleted_ids=deleted_ids, delete_source=delete_source,
    )
    summary["document"] = document
    # `reconciled` means the device NAMED deletions on this push, not that any
    # row matched — naming an id that is already hidden or unknown is a no-op
    # and still reports reconciled with `deleted: 0`. Under #906 it meant "the
    # client declared itself complete", which no longer exists.
    summary["reconciled"] = bool(deleted_ids)
    summary["calibre_book_id"] = book_id
    summary["matched"] = True
    return create_sync_response(summary)
