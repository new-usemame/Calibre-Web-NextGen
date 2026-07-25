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


def _loggable(value, limit=80):
    """Render a device-supplied value safe to put in a log line.

    The rejection paths below log the field they refused, and by definition
    that field failed validation — a `document` containing a newline would
    otherwise let a device forge log lines, which is exactly the surface a
    reporter and we both read to diagnose a sync. ``repr`` escapes the
    separators and the cap keeps one bad push from flooding the log.
    """
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "...'"


def _describe_count(value):
    """How many entries the device sent, for a value that has NOT been shape-
    checked yet.

    The unmatched-book reply is returned *before* the ``annotations`` /
    ``deleted`` validation runs, so this sees whatever JSON carried — a scalar,
    a bool, an object. A bare ``len()`` there turns a request that has always
    answered 200 into a 500, and counting a dict's keys reports "2
    annotation(s)" for something that is not an annotation array at all. Report
    a count only for a real array, and name the shape otherwise.
    """
    if isinstance(value, list):
        return str(len(value))
    if value is None:
        return "0"
    return "0 (not an array: %s)" % type(value).__name__


def _reject(user, document, error, message, status=400):
    """Refuse a device push, and say so in the log.

    KOReader shows the user one bit — "Server push failed" — and the plugin has
    only the HTTP status to go on, so it can never report *which* field the
    server objected to. The server log is the sole diagnostic surface for a
    highlight-sync report, and it used to be empty for every path below: #920
    took three wrong diagnoses and ten days of the reporter's testing precisely
    because a rejected push left no trace to send us. Every rejection returns
    through here so that can't regress to silence one branch at a time.
    """
    log.info(
        "KOReader annotation push rejected: user=%s document=%s error=%s (%s)",
        getattr(user, "id", "?"), _loggable(document), error, message,
    )
    return create_sync_response({"error": error, "message": message}, status)


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
        return _reject(user, document, ERROR_DOCUMENT_FIELD_MISSING, "Invalid document field")

    book_id, _fmt, _title, _path, _ver = get_book_by_checksum(document)
    if not book_id:
        # Unknown book: empty set, not an error (the device may have a book the
        # server doesn't know yet). Logged because from the device's side this
        # is indistinguishable from "the server has no highlights for me", and
        # it is the usual shape of a book that was never checksum-registered.
        log.info(
            "KOReader annotation pull: user=%s document=%s no matching book "
            "(returning empty set)", user.id, _loggable(document),
        )
        return create_sync_response({"document": document, "annotations": [], "annotation_count": 0})

    payload = build_pull_payload(user.id, book_id, ub.session)
    payload["document"] = document
    payload["calibre_book_id"] = book_id
    log.info(
        "KOReader annotation pull: user=%s book=%s document=%s annotations=%s",
        user.id, book_id, _loggable(document), payload.get("annotation_count", 0),
    )
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
        return _reject(user, "?", "invalid_payload", "JSON object required")
    document = data.get("document")
    if not is_valid_key_field(document):
        return _reject(user, document, ERROR_DOCUMENT_FIELD_MISSING, "Invalid document field")

    def _unmatched():
        # HTTP 200 with `matched: false`, so the device reports the sync as a
        # success to the user while the server saved nothing at all. That is a
        # book the library has no checksum registered for — the most common
        # cause of "my highlights sync but never show up" — and it has to be
        # loud, because the device itself will never mention it.
        log.warning(
            "KOReader annotation push: user=%s document=%s matched NO book — "
            "%s annotation(s) and %s delete(s) were NOT saved",
            user.id, _loggable(document),
            _describe_count(data.get("annotations")),
            _describe_count(data.get("deleted")),
        )
        return create_sync_response({"document": document, "matched": False,
                                     "created": 0, "updated": 0, "deleted": 0, "skipped": 0})

    book_id, _fmt, _title, _path, _ver = get_book_by_checksum(document)
    if not book_id:
        return _unmatched()

    from ... import calibre_db
    book = calibre_db.get_book(book_id)
    if book is None:
        return _unmatched()

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
        return _reject(user, document, "invalid_annotations",
                       "annotations must be an array")

    deleted_ids = data.get("deleted")
    if deleted_ids == {} or deleted_ids is None:
        deleted_ids = []
    if not isinstance(deleted_ids, list) or any(
        not isinstance(aid, str) or not aid.strip() for aid in deleted_ids
    ):
        return _reject(user, document, "invalid_deleted",
                       "deleted must be an array of annotation_id strings")

    # Only meaningful when something is being deleted. Rejecting it on a push
    # that deletes nothing would throw away the annotations that push carries
    # over a field with no effect.
    delete_source = data.get("delete_source", "koreader")
    if deleted_ids and (
        not isinstance(delete_source, str) or delete_source not in _DELETABLE_SOURCES
    ):
        return _reject(
            user, document, "invalid_delete_source",
            "delete_source must be one of: %s" % ", ".join(sorted(_DELETABLE_SOURCES)),
        )
    from ...services.annotation_portable import validate_portable_payload
    for index, payload in enumerate(annotations):
        error = validate_portable_payload(payload)
        if error:
            return _reject(user, document, "invalid_annotation",
                           f"annotations[{index}]: {error}")

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
    log.info(
        "KOReader annotation push: user=%s book=%s document=%s "
        "created=%s updated=%s deleted=%s skipped=%s (pushed=%s named_deletes=%s)",
        user.id, book_id, _loggable(document),
        summary.get("created", 0), summary.get("updated", 0),
        summary.get("deleted", 0), summary.get("skipped", 0),
        len(annotations), len(deleted_ids),
    )
    if summary.get("skipped"):
        # A skipped annotation is one the server declined to store while still
        # answering 200, so the device tells the user it synced and the
        # highlight is simply gone. Never let that be invisible.
        log.warning(
            "KOReader annotation push: user=%s book=%s document=%s skipped %s of "
            "%s pushed annotation(s) — they were NOT stored",
            user.id, book_id, _loggable(document), summary["skipped"], len(annotations),
        )
    if deleted_ids and not summary.get("deleted"):
        # The device named deletions and none matched a live row. Either they
        # were already tombstoned (benign, a repeat sync) or the device's ids
        # don't correspond to anything the server holds for this book — which
        # is the "deleting on the device does nothing" report.
        log.info(
            "KOReader annotation push: user=%s book=%s document=%s named %s "
            "delete(s) that matched no live %s row: %s",
            user.id, book_id, _loggable(document), len(deleted_ids), delete_source,
            ", ".join(_loggable(a) for a in sorted(deleted_ids)[:10]),
        )
    return create_sync_response(summary)
