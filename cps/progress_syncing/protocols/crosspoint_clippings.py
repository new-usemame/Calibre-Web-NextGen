#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""CrossPoint clippings: back up a CrossPoint reader's highlights, and give
them back to the reader that made them.

CrossPoint's Sync Clippings calls two routes under the KOSync base before its
progress request, and any failure stops Sync Progress:

    GET /kosync/api/v1/clippings/<document>  -> probe, then pull (server -> reader)
    PUT /kosync/api/v1/clippings/<document>  -> upload and delete (reader -> server)

The API is the one of CrossPoint's own sync server, crosspoint-sync
(``src/routes/v1/clippings.ts``). Login and book lookup are those of
``/kosync/syncs/annotations``.

Each clipping is an ``annotation`` row of source ``crosspoint``, stored
through ``koreader_annotations.apply_push`` with a ``text_quote``, so it is
placed in the library EPUB, shown in the web reader and sent to sync targets.
Its CrossPoint layout fields live in ``ub.CrossPointClipping`` and go back
exactly as sent: the firmware replaces its own record with the server copy on
the first pull after an upload, so a changed field would move the highlight.

The firmware pulls one item per request with ``revision > cursor`` and fails
the whole sync on any rule it does not like; each rule it checks is kept
here. See notes/CROSSPOINT-CLIPPINGS-DESIGN.md.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from flask import request
from sqlalchemy import insert, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ... import csrf, logger, ub
from .kosync import (
    kosync,
    authenticate_user,
    create_sync_response,
    is_valid_key_field,
    _require_kosync_enabled,
    MAX_INVENTORY_BYTES,
    ERROR_INTERNAL,
    ERROR_UNAUTHORIZED_USER,
    ERROR_DOCUMENT_FIELD_MISSING,
)
from .koreader_annotations import _book_for_document, _loggable, apply_push

log = logger.create()

SOURCE = "crosspoint"
SYNC_VERSION = 2
# The limits of crosspoint-sync, except the body, which is kosync's own.
MAX_PUT_ITEMS = 50
MAX_PUT_BYTES = MAX_INVENTORY_BYTES
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
MAX_CHAPTER_CHARS = 64
# The firmware's own limits (ClippingSync.cpp, ClippingStore.h).
MAX_ID_CHARS = 64
MAX_TEXT_BYTES = 4096
UINT16 = 0xFFFF
UINT32 = 0xFFFFFFFF
MAX_CURSOR = 2 ** 63 - 1

_UINT16_FIELDS = ("start_page", "end_page", "start_word", "end_word", "words")
_LAYOUT_FIELDS = ("spine", "start_page", "end_page", "pages", "start_word", "end_word",
                  "words", "para", "chapter", "text", "created_at", "layout_signature",
                  "start_offset", "end_offset")


def _now():
    return datetime.now(timezone.utc)


def _epoch(value):
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def _uint(value, limit):
    # bool is an int in Python; the firmware refuses a JSON true or false.
    return (isinstance(value, int) and not isinstance(value, bool)
            and 0 <= value <= limit)


def _utf8_bytes(value):
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError:  # a lone surrogate from a \\ud800 escape
        return None


# ---------------------------------------------------------------------------
# Testable core
# ---------------------------------------------------------------------------


def parse_item(raw):
    """``(item, None)`` for a valid uploaded item, else ``(None, error)``.

    At least as strict as the firmware's decode of a pulled item: a value
    stored here that the firmware refuses on a pull would fail every later
    sync of the book, and the reader cannot recover from that.
    """
    if not isinstance(raw, dict):
        return None, "item must be an object"
    clip_id = raw.get("id")
    if (not isinstance(clip_id, str) or not 1 <= len(clip_id) <= MAX_ID_CHARS
            or any(not "!" <= ch <= "~" for ch in clip_id)):
        return None, "id must be 1-%d printable ASCII characters" % MAX_ID_CHARS
    deleted = raw.get("deleted", 0)
    if isinstance(deleted, bool):
        deleted = int(deleted)
    if not _uint(deleted, 1):
        return None, "deleted must be 0, 1, false or true"
    if deleted:
        return {"id": clip_id, "deleted": True}, None

    item = {"id": clip_id, "deleted": False}
    if not _uint(raw.get("spine"), UINT16):
        return None, "spine must be an unsigned 16-bit integer"
    item["spine"] = raw["spine"]
    for field in _UINT16_FIELDS:
        value = raw.get(field, 0)
        if not _uint(value, UINT16):
            return None, "%s must be an unsigned 16-bit integer" % field
        item[field] = value
    pages = raw.get("pages", 1)
    if not _uint(pages, UINT16):
        return None, "pages must be an unsigned 16-bit integer"
    item["pages"] = pages
    para = raw.get("para")
    if para is not None and not _uint(para, UINT16):
        return None, "para must be null or an unsigned 16-bit integer"
    item["para"] = para
    for field in ("created_at", "layout_signature"):
        value = raw.get(field, 0)
        if not _uint(value, UINT32):
            return None, "%s must be an unsigned 32-bit integer" % field
        item[field] = value

    start, end = raw.get("start_offset"), raw.get("end_offset")
    if start is None and end is None:
        item["start_offset"] = item["end_offset"] = None
    elif (not _uint(start, UINT32) or not _uint(end, UINT32)
          or start >= end or end == UINT32):
        return None, "start_offset and end_offset must both be null, or offsets with start < end"
    else:
        item["start_offset"], item["end_offset"] = start, end

    text = raw.get("text")
    size = _utf8_bytes(text) if isinstance(text, str) else None
    if not text or size is None or size > MAX_TEXT_BYTES or "\x00" in text:
        return None, "text must be 1-%d UTF-8 bytes with no NUL" % MAX_TEXT_BYTES
    item["text"] = text

    chapter = raw.get("chapter")
    if chapter is None:
        chapter = ""
    if not isinstance(chapter, str) or _utf8_bytes(chapter) is None:
        return None, "chapter must be a string"
    item["chapter"] = chapter[:MAX_CHAPTER_CHARS]
    return item, None


def next_revision(session, user_id) -> int:
    """The next revision of ``user_id``'s clippings. Does not commit.

    The UPDATE takes SQLite's write lock, so two writers never get one value.
    The first clipping of an account inserts the clock; losing that race
    rolls back and takes the winner's row. The caller makes nothing pending
    before its first revision, so the rollback loses nothing.
    """
    clock = ub.CrossPointClippingClock.__table__
    for _attempt in range(3):
        changed = session.execute(
            update(clock).where(clock.c.user_id == user_id)
            .values(revision=clock.c.revision + 1)).rowcount
        if changed:
            return session.execute(
                select(clock.c.revision).where(clock.c.user_id == user_id)).scalar_one()
        try:
            session.execute(insert(clock).values(user_id=user_id, revision=1))
            return 1
        except IntegrityError:
            session.rollback()
    raise RuntimeError("could not advance the CrossPoint clipping clock")


def _served_rows_query(session, user_id, book_id, device, has_header):
    """``(annotation, sidecar)`` rows a reader may pull: its own and those of
    no device when it names itself, every one of the account when it does not
    (the own-device rule of v4.1.48; crosspoint-sync is per account)."""
    query = (session.query(ub.Annotation, ub.CrossPointClipping)
             .join(ub.CrossPointClipping,
                   ub.CrossPointClipping.annotation_id == ub.Annotation.id)
             .filter(ub.Annotation.user_id == user_id,
                     ub.Annotation.book_id == book_id,
                     ub.Annotation.source == SOURCE))
    if has_header:
        if device is not None:
            query = query.filter(or_(ub.Annotation.origin_device_id == device.id,
                                     ub.Annotation.origin_device_id.is_(None)))
        else:
            query = query.filter(ub.Annotation.origin_device_id.is_(None))
    return query


def _tombstone(session, user_id, sidecar):
    revision = next_revision(session, user_id)
    sidecar.deleted = True
    sidecar.updated_at = _now()
    sidecar.sync_revision = revision


def reconcile_hidden(session, user_id, book_id, device, has_header) -> int:
    """Tombstone each served clipping hidden elsewhere (a web reader delete),
    so the reader removes it too. Returns how many; does not commit."""
    rows = (_served_rows_query(session, user_id, book_id, device, has_header)
            .filter(ub.Annotation.hidden == True,  # noqa: E712 - SQLAlchemy idiom
                    ub.CrossPointClipping.deleted == False)  # noqa: E712
            .order_by(ub.CrossPointClipping.sync_revision).all())
    for _row, sidecar in rows:
        _tombstone(session, user_id, sidecar)
    return len(rows)


def _wire_item(row, sidecar, reader_format) -> dict:
    """One pulled item: a tombstone, or the layout exactly as uploaded."""
    if sidecar.deleted:
        return {"id": row.annotation_id, "revision": sidecar.sync_revision,
                "deleted": 1, "updated_at": _epoch(sidecar.updated_at)}
    item = {
        "id": row.annotation_id,
        "revision": sidecar.sync_revision,
        "spine": sidecar.spine,
        "start_page": sidecar.start_page,
        "end_page": sidecar.end_page,
        "pages": sidecar.pages,
        "start_word": sidecar.start_word,
        "end_word": sidecar.end_word,
        "words": sidecar.words,
        "para": sidecar.para,
        "chapter": sidecar.chapter,
        "text": sidecar.text,
        "created_at": sidecar.created_epoch,
        # The integer: the firmware refuses a JSON boolean here.
        "deleted": 0,
        "updated_at": _epoch(sidecar.updated_at),
        "layout_signature": sidecar.layout_signature,
        "start_offset": sidecar.start_offset,
        "end_offset": sidecar.end_offset,
    }
    if not reader_format:
        from ...services.annotation_colors import to_display_name
        item["note"] = row.note_text
        item["color"] = to_display_name(row.highlight_color)
    return item


def build_page(session, *, user_id, book_id, device, has_header, cursor, limit,
               reader_format) -> dict:
    """``cursor``, ``more`` and ``items`` of one pull, after any reconcile.

    Commits only when a web reader delete was turned into a tombstone.
    """
    if reconcile_hidden(session, user_id, book_id, device, has_header):
        session.commit()
    rows = (_served_rows_query(session, user_id, book_id, device, has_header)
            .filter(ub.CrossPointClipping.sync_revision > cursor)
            .order_by(ub.CrossPointClipping.sync_revision)
            .limit(limit + 1).all())
    more = len(rows) > limit
    rows = rows[:limit]
    items = [_wire_item(row, sidecar, reader_format) for row, sidecar in rows]
    return {
        "cursor": rows[-1][1].sync_revision if rows else cursor,
        "more": more,
        "items": items,
        "tombstones": sum(1 for _row, sidecar in rows if sidecar.deleted),
    }


def _find_row(session, user_id, book_id, clip_id):
    return (session.query(ub.Annotation)
            .filter(ub.Annotation.user_id == user_id,
                    ub.Annotation.book_id == book_id,
                    ub.Annotation.annotation_id == clip_id)
            .first())


def _sidecar_values(item):
    values = {field: item[field] for field in _LAYOUT_FIELDS if field != "created_at"}
    values["created_epoch"] = item["created_at"]
    return values


def _portable(item) -> dict:
    from ...services.text_anchor import MAX_QUOTE_CHARS, parse_quote
    payload = {"annotation_id": item["id"], "source": SOURCE, "type": "highlight",
               "highlighted_text": item["text"]}
    # Placement takes at most MAX_QUOTE_CHARS; the sidecar keeps every word.
    quote = {"exact": item["text"][:MAX_QUOTE_CHARS]}
    if parse_quote(quote) is not None:
        payload["text_quote"] = quote
    return payload


def _apply_upload(item, *, user, book, document, device_id, session, epub_path,
                  counts):
    row = _find_row(session, user.id, book.id, item["id"])
    if row is not None and row.source != SOURCE:
        log.warning(
            "CrossPoint clippings push: user=%s book=%s id=%s belongs to source=%s "
            "device=%s - not changed",
            user.id, book.id, _loggable(item["id"]), row.source, row.origin_device_id)
        counts["unchanged"] += 1
        return
    summary = apply_push([_portable(item)], user=user, book=book, session=session,
                         commit=ub.session_commit, delete_source=SOURCE,
                         origin_device_id=device_id, epub_path=epub_path)
    row = _find_row(session, user.id, book.id, item["id"])
    if row is None:
        counts["unchanged"] += 1
        return
    if row.hidden:
        log.info("CrossPoint clippings push: user=%s book=%s id=%s is deleted on the "
                 "server - not restored", user.id, book.id, _loggable(item["id"]))
        counts["unchanged"] += 1
        return
    if (device_id is not None and row.origin_device_id is not None
            and row.origin_device_id != device_id):
        log.warning(
            "CrossPoint clippings push: user=%s book=%s id=%s belongs to source=%s "
            "device=%s - not changed",
            user.id, book.id, _loggable(item["id"]), row.source, row.origin_device_id)
        counts["unchanged"] += 1
        return

    values = _sidecar_values(item)
    sidecar = row.crosspoint_clipping
    if sidecar is not None and all(getattr(sidecar, k) == v for k, v in values.items()):
        counts["unchanged"] += 1
        return
    # The revision first, before anything is pending (next_revision).
    revision = next_revision(session, user.id)
    if sidecar is None:
        sidecar = ub.CrossPointClipping(annotation_id=row.id, user_id=user.id)
        row.crosspoint_clipping = sidecar
        counts["created"] += 1
    else:
        counts["updated"] += 1
    for key, value in values.items():
        setattr(sidecar, key, value)
    sidecar.document = document
    sidecar.deleted = False
    sidecar.updated_at = _now()
    sidecar.sync_revision = revision
    if summary.get("created") and item["created_at"]:
        row.created_at = datetime.fromtimestamp(item["created_at"], timezone.utc)
    if not ub.session_commit():
        raise RuntimeError("database commit did not land")


def _apply_delete(item, *, user, book, device_id, session, counts):
    apply_push([], user=user, book=book, session=session, commit=ub.session_commit,
               deleted_ids=[item["id"]], delete_source=SOURCE,
               origin_device_id=device_id)
    row = _find_row(session, user.id, book.id, item["id"])
    sidecar = row.crosspoint_clipping if row is not None and row.source == SOURCE else None
    if sidecar is None or not row.hidden or sidecar.deleted:
        counts["unchanged"] += 1
        return
    _tombstone(session, user.id, sidecar)
    if not ub.session_commit():
        raise RuntimeError("database commit did not land")
    counts["deleted"] += 1


def apply_items(items, *, user, book, document, device_id, session, epub_path) -> dict:
    """Store parsed items one at a time; return ``accepted`` and the counts.

    Every valid item counts as accepted, also one that changed nothing (a
    repeat, a tombstoned id, another reader's id, an unknown delete): a retry
    cannot change its result, and a reader that saw it refused would retry it
    on every sync and fail each time.
    """
    counts = {"created": 0, "updated": 0, "deleted": 0, "unchanged": 0}
    for item in items:
        if item["deleted"]:
            _apply_delete(item, user=user, book=book, device_id=device_id,
                          session=session, counts=counts)
        else:
            _apply_upload(item, user=user, book=book, document=document,
                          device_id=device_id, session=session, epub_path=epub_path,
                          counts=counts)
    counts["accepted"] = len(items)
    return counts


# ---------------------------------------------------------------------------
# Routes (thin; reuse kosync auth + book resolution)
# ---------------------------------------------------------------------------


def _reject(user, document, error, message, status=400):
    """Refuse a request, and say so in the log: the reader shows only
    "Clipping Sync Failed"."""
    log.info(
        "CrossPoint clippings %s rejected: user=%s document=%s error=%s (%s)",
        "pull" if request.method == "GET" else "push",
        getattr(user, "id", "?"), _loggable(document), error, message,
    )
    return create_sync_response({"error": error, "message": message}, status)


def _start(document):
    """``(user, None)`` when the request may go on, else ``(user, response)``."""
    blocked = _require_kosync_enabled()
    if blocked:
        return None, blocked
    user = authenticate_user()
    if not user:
        return None, create_sync_response(
            {"error": ERROR_UNAUTHORIZED_USER, "message": "Unauthorized"}, 401)
    if not is_valid_key_field(document):
        return user, _reject(user, document, ERROR_DOCUMENT_FIELD_MISSING,
                             "Invalid document field")
    return user, None


def _cursor_arg():
    raw = request.args.get("cursor")
    if raw is None:
        return 0
    if not raw.isascii() or not raw.isdigit() or int(raw) > MAX_CURSOR:
        return None
    return int(raw)


def _limit_arg():
    try:
        limit = int(request.args.get("limit", DEFAULT_LIMIT))
    except ValueError:
        return DEFAULT_LIMIT
    if limit < 1:
        return DEFAULT_LIMIT
    return min(limit, MAX_LIMIT)


@csrf.exempt
@kosync.route("/kosync/api/v1/clippings/<document>", methods=["GET"])
def pull_clippings(document: str):
    """Probe and pull one book's clippings (server -> reader)."""
    user, refused = _start(document)
    if refused:
        return refused
    cursor = _cursor_arg()
    if cursor is None:
        return _reject(user, document, "invalid_cursor",
                       "cursor must be an unsigned integer")
    limit = _limit_arg()
    reader_format = request.args.get("format") == "reader"
    page = {"document": document, "sync_version": SYNC_VERSION, "cursor": cursor,
            "until": int(time.time()), "more": False, "items": []}

    book_id, _fmt = _book_for_document(document, user)
    if not book_id:
        log.info("CrossPoint clippings pull: user=%s document=%s no matching book "
                 "(returning empty set, book_known=false)", user.id, _loggable(document))
        page["book_known"] = False
        return create_sync_response(page)

    from ...services.kosync_place import request_device
    device, _identified, raw_id = request_device(user.id, observe=False)
    try:
        built = build_page(ub.session, user_id=user.id, book_id=book_id, device=device,
                           has_header=bool(raw_id), cursor=cursor, limit=limit,
                           reader_format=reader_format)
    except (RuntimeError, SQLAlchemyError):
        ub.session.rollback()
        log.exception("CrossPoint clippings pull database write failed: user=%s book=%s",
                      user.id, book_id)
        return _reject(user, document, ERROR_INTERNAL, "Database error", status=503)
    tombstones = built.pop("tombstones")
    page.update(built)
    page["book_known"] = True
    page["calibre_book_id"] = book_id
    # One sync reads every clipping, one request each: only the first page is INFO.
    log.log(logging.INFO if cursor == 0 else logging.DEBUG,
            "CrossPoint clippings pull: user=%s book=%s document=%s cursor=%s "
            "served=%s tombstones=%s",
            user.id, book_id, _loggable(document), cursor,
            len(page["items"]), tombstones)
    return create_sync_response(page)


@csrf.exempt
@kosync.route("/kosync/api/v1/clippings/<document>", methods=["PUT"])
def push_clippings(document: str):
    """Upload and delete clippings (reader -> server)."""
    user, refused = _start(document)
    if refused:
        return refused
    if request.content_length is not None and request.content_length > MAX_PUT_BYTES:
        return _reject(user, document, "invalid_payload", "Payload is too large")
    data = request.get_json(silent=True)
    raw_items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= MAX_PUT_ITEMS:
        return _reject(user, document, "invalid_items",
                       "items must be an array of 1-%d items" % MAX_PUT_ITEMS)
    items = []
    for index, raw in enumerate(raw_items):
        item, error = parse_item(raw)
        if error:
            return _reject(user, document, "invalid_item", "items[%d]: %s" % (index, error))
        items.append(item)

    reply = {"document": document, "until": int(time.time())}
    book_id, _fmt = _book_for_document(document, user)
    from ... import calibre_db
    book = calibre_db.get_book(book_id) if book_id else None
    if book is None:
        # Accepted and dropped: the reader marks these as uploaded and never
        # sends them again, so this line is their only trace.
        log.warning("CrossPoint clippings push: user=%s document=%s matched NO book - "
                    "%s item(s) were NOT saved", user.id, _loggable(document), len(items))
        reply.update(accepted=len(items), matched=False)
        return create_sync_response(reply)

    from ...services.kosync_place import request_device
    device, _identified, _raw_id = request_device(user.id, observe=True)
    epub_path = None
    if any(not item["deleted"] for item in items):
        # Placing a quote reads the book's text (see the annotations push).
        from .kosync import _readable_epub
        epub_path = _readable_epub(book_id, user)
    try:
        counts = apply_items(items, user=user, book=book, document=document,
                             device_id=device.id if device is not None else None,
                             session=ub.session, epub_path=epub_path)
    except (RuntimeError, SQLAlchemyError):
        ub.session.rollback()
        log.exception("CrossPoint clippings push database write failed: user=%s book=%s "
                      "document=%s", user.id, book_id, _loggable(document))
        return _reject(user, document, ERROR_INTERNAL, "Database error", status=503)
    log.info("CrossPoint clippings push: user=%s book=%s document=%s accepted=%s "
             "created=%s updated=%s deleted=%s unchanged=%s",
             user.id, book_id, _loggable(document), counts["accepted"],
             counts["created"], counts["updated"], counts["deleted"], counts["unchanged"])
    reply.update(accepted=counts["accepted"], matched=True, calibre_book_id=book_id)
    return create_sync_response(reply)
