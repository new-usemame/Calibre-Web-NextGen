# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Annotations blueprint — H1 Phases 3 + 4.

User-facing routes for the Kobo highlight feature. P3 ships the import
endpoint; P4 adds the view + export surface; P5 adds the web-reader
create/edit path.

Routes shipped so far:

    GET  /annotations/import                 -> upload form           (P3)
    POST /annotations/import                 -> ingest .sqlite        (P3)
    GET  /annotations/<book_id>              -> per-book view         (P4)
    GET  /annotations/<book_id>/export.md    -> Markdown download     (P4)
    GET  /annotations/<book_id>/export.csv   -> CSV download          (P4)
    GET  /annotations/<book_id>/export.json  -> JSON download         (P4)

Auth: ``@user_login_required`` — annotation data is per-user-private.

CSRF: protected via Flask-WTF's global middleware on mutating routes
(POST /annotations/import). Export GETs are idempotent and need no CSRF.

The import path NEVER persists the uploaded SQLite to disk. The file is
parsed in-place via a temp file that's deleted before the request
returns. The file's contents are PII (the user's reading history,
search queries, every bookmark they ever made) — we read what we need
+ throw away the rest.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from flask import Blueprint, Response, abort, g, jsonify, request, url_for
from flask_babel import gettext as _
from sqlalchemy import and_, case, false, func, literal, or_, select, union_all
from sqlalchemy.exc import SQLAlchemyError

from . import calibre_db, logger, ub
from .admin import admin_required
from .cw_login import current_user
from .db import FilteredBookVisibilityUnavailable
from .render_template import render_title_template
from .services.annotation_types import (
    to_storage_type,
    type_for_webreader_annotation,
)
from .services.annotation_colors import (
    WEBREADER_COLOR_NAMES,
    to_display_name,
    to_storage_color,
)
from .services import device_capabilities
from .services.kobo_import import (
    KoboUploadError,
    MAX_KOBO_DATABASE_UPLOAD_BYTES,
    parse_kobo_bookmarks,
    temporary_kobo_database,
)
from .usermanagement import user_login_required

log = logger.create()

annotations_bp = Blueprint("annotations", __name__)

# Defense-in-depth file-size cap. Typical real-device KoboReader.sqlite
# files are 30-50 MB; reject anything over 100 MB.
MAX_UPLOAD_BYTES = MAX_KOBO_DATABASE_UPLOAD_BYTES

# Public contract for the per-device inventory endpoint. Keep the server-side
# default bounded even when a caller omits pagination entirely.
DEFAULT_DEVICE_INVENTORY_LIMIT = 200
MAX_DEVICE_INVENTORY_LIMIT = 200
DEFAULT_DEVICE_LIST_LIMIT = 100
MAX_DEVICE_LIST_LIMIT = 200
DEFAULT_DEVICE_POSITION_LIMIT = 100
MAX_DEVICE_POSITION_LIMIT = 200
DEFAULT_ADMIN_DEVICE_LIMIT = 50
MAX_ADMIN_DEVICE_LIMIT = 200

# Device-detail annotations deliberately expose a fixed page size.  The client
# can choose the page, but cannot turn a user-facing read into an unbounded
# export by supplying its own limit.
DEVICE_ANNOTATION_PAGE_SIZE = 50
# SQLite uses signed 64-bit row counts/offsets.  Deriving this public bound
# from that storage limit keeps every page a true total can report reachable.
MAX_DEVICE_ANNOTATION_PAGE = ((1 << 63) - 1) // DEVICE_ANNOTATION_PAGE_SIZE + 1
DEVICE_ANNOTATION_TYPES = ("highlight", "note", "dogear")
DEVICE_ANNOTATION_ROLES = ("origin", "assigned")
AUTHORITY_STATUSES = (
    "unseeded", "seeding", "authoritative", "quarantined", "disabled",
)


def _commit_required(commit):
    """Raise when CWNG's commit wrapper reports a rolled-back write."""
    if commit() is False:
        raise RuntimeError("database commit did not land")


def _database_error_response(operation):
    """Roll back a failed API write and keep the response JSON-shaped."""
    try:
        ub.session.rollback()
    except Exception:
        log.exception("annotations: rollback failed after %s", operation)
    log.exception("annotations: %s failed", operation)
    return jsonify({"error": "database_error"}), 500


def _visibility_unavailable_response(operation, error):
    """Expose a fail-closed visibility-policy outage without faking an empty view."""
    try:
        ub.session.rollback()
    except Exception:
        log.exception("annotations: rollback failed after %s", operation)
    log.error("annotations: %s visibility unavailable: %s", operation, error)
    return jsonify({
        "error": "visibility_unavailable",
        "message": "The filtered library view is temporarily unavailable.",
        "retryable": True,
    }), 503


def _offset_pagination(*, default_limit, max_limit):
    """Parse the shared strict limit/offset collection contract."""
    raw_limit = request.args.get("limit")
    raw_offset = request.args.get("offset")
    try:
        limit = default_limit if raw_limit is None else int(raw_limit)
    except (TypeError, ValueError):
        return None, jsonify({
            "error": "invalid_pagination",
            "field": "limit",
            "message": f"limit must be an integer between 1 and {max_limit}",
            "max_limit": max_limit,
        }), 400
    if not 1 <= limit <= max_limit:
        return None, jsonify({
            "error": "invalid_pagination",
            "field": "limit",
            "message": f"limit must be an integer between 1 and {max_limit}",
            "max_limit": max_limit,
        }), 400
    try:
        offset = 0 if raw_offset is None else int(raw_offset)
    except (TypeError, ValueError):
        return None, jsonify({
            "error": "invalid_pagination",
            "field": "offset",
            "message": "offset must be a non-negative integer",
        }), 400
    if offset < 0:
        return None, jsonify({
            "error": "invalid_pagination",
            "field": "offset",
            "message": "offset must be a non-negative integer",
        }), 400
    return (limit, offset), None, None


def _device_inventory_pagination():
    return _offset_pagination(
        default_limit=DEFAULT_DEVICE_INVENTORY_LIMIT,
        max_limit=MAX_DEVICE_INVENTORY_LIMIT,
    )


def _owned_device(public_id, user_id, session):
    return session.query(ub.Device).filter(
        ub.Device.public_id == public_id, ub.Device.user_id == user_id,
    ).first()


def _device_kind_label(kind):
    return {
        "kobo": "Kobo",
        "koreader": "KOReader",
        "webreader": "Web reader",
    }.get(kind, "E-reader")


def _device_timestamp_json(value):
    """Serialize database datetimes as unambiguous UTC API instants.

    SQLite returns naive values for these columns. They represent UTC, so
    attaching UTC here prevents JavaScript from interpreting them in the
    browser's local timezone. Aware values are normalized to the same contract.
    """
    if value is None or not isinstance(value, datetime):
        return value
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


def _empty_authority_rollup():
    return {**{status: 0 for status in AUTHORITY_STATUSES}, "books_partially_seeded": 0}


def _device_json(device, annotation_count=0, inventory_report=None, storage_snapshot=None,
                 annotation_counts=None, authority_rollup=None, seeded_books=0,
                 unseeded_books=0, inventory_count=None, books_with_position=0,
                 last_position_at=None):
    annotation_counts = annotation_counts or {}
    return {
        "public_id": device.public_id,
        "label": device.display_name or _device_kind_label(device.kind),
        "type": device.kind,
        "kind": device.kind,
        "kind_label": _device_kind_label(device.kind),
        "model": device.model,
        "firmware": device.firmware_version,
        "first_seen": _device_timestamp_json(device.first_seen_at),
        "last_seen": _device_timestamp_json(device.last_seen_at),
        "annotation_count": int(annotation_count),
        "highlights": int(annotation_counts.get("highlight", 0)),
        "notes": int(annotation_counts.get("note", 0)),
        "dogears": int(annotation_counts.get("dogear", 0)),
        "inventory_count": int(
            inventory_count if inventory_count is not None
            else inventory_report.item_count if inventory_report else 0
        ),
        "inventory_observed": _device_timestamp_json(
            inventory_report.observed_at if inventory_report else None
        ),
        "storage_free": storage_snapshot.free_bytes if storage_snapshot else None,
        "storage_total": storage_snapshot.total_bytes if storage_snapshot else None,
        "storage_observed": _device_timestamp_json(
            storage_snapshot.observed_at if storage_snapshot else None
        ),
        "seeded_books": int(seeded_books),
        "unseeded_books": int(unseeded_books),
        "books_with_position": int(books_with_position),
        "last_position_at": _device_timestamp_json(last_position_at),
        "authority": authority_rollup or _empty_authority_rollup(),
        "can_receive_books": device.kind in ("kobo", "koreader"),
        "active": bool(device.active),
    }


def _device_owner(device, session):
    return session.query(ub.User).filter(ub.User.id == device.user_id).first()


def _visible_books_for_owner(owner, book_ids):
    """Resolve metadata for the owner's global annotation archive.

    My Library membership is a catalogue curation boundary, not ownership of
    reading data.  Keep every other live content restriction in force while
    allowing annotations for a removed book to remain readable.
    """
    if owner is None:
        log.error("annotations: device owner unavailable; filtered book view denied")
        raise FilteredBookVisibilityUnavailable("device owner unavailable")
    books = {}
    for book_id in sorted({int(value) for value in book_ids if value is not None}):
        book = calibre_db.get_filtered_book(
            book_id, allow_show_global=True, user=owner,
        )
        if book is not None:
            books[book_id] = book
    return books


def _book_in_scope(column, visible_ids):
    """Return a compact SQL predicate for a request-snapshot visibility set."""
    if not visible_ids:
        return false()
    values = func.json_each(json.dumps(sorted(visible_ids))).table_valued("value").alias()
    return column.in_(select(values.c.value))


def _owner_book_in_scope(owner_column, book_column, scopes):
    clauses = [
        and_(owner_column == owner_id, _book_in_scope(book_column, visible_ids))
        for owner_id, visible_ids in scopes.items()
    ]
    return or_(*clauses) if clauses else false()


def _device_book_in_scope(device_column, book_column, devices, scopes):
    clauses = [
        and_(
            device_column == device.id,
            _book_in_scope(book_column, scopes.get(int(device.user_id), frozenset())),
        )
        for device in devices
    ]
    return or_(*clauses) if clauses else false()


def _owner_visibility_state(session, candidates_by_owner):
    """Prefetch only restriction rows that can affect device-data candidates."""
    candidates_by_owner = {
        int(owner_id): frozenset(int(book_id) for book_id in book_ids)
        for owner_id, book_ids in candidates_by_owner.items()
    }
    owner_ids = tuple(sorted(candidates_by_owner))
    state = {
        owner_id: {"archived": set(), "hidden": set(), "membership": set()}
        for owner_id in owner_ids
    }
    if not owner_ids:
        return state
    for user_id, book_id in session.query(
            ub.ArchivedBook.user_id, ub.ArchivedBook.book_id).filter(
                ub.ArchivedBook.user_id.in_(owner_ids),
                ub.ArchivedBook.is_archived.is_(True),
                _owner_book_in_scope(
                    ub.ArchivedBook.user_id,
                    ub.ArchivedBook.book_id,
                    candidates_by_owner,
                ),
            ).all():
        state[int(user_id)]["archived"].add(int(book_id))
    for user_id, book_id in session.query(
            ub.UserHiddenBook.user_id, ub.UserHiddenBook.book_id).filter(
                ub.UserHiddenBook.user_id.in_(owner_ids),
                _owner_book_in_scope(
                    ub.UserHiddenBook.user_id,
                    ub.UserHiddenBook.book_id,
                    candidates_by_owner,
                ),
            ).all():
        state[int(user_id)]["hidden"].add(int(book_id))
    for user_id, book_id in session.query(
            ub.UserLibraryBook.user_id, ub.UserLibraryBook.book_id).filter(
                ub.UserLibraryBook.user_id.in_(owner_ids),
                _owner_book_in_scope(
                    ub.UserLibraryBook.user_id,
                    ub.UserLibraryBook.book_id,
                    candidates_by_owner,
                ),
            ).all():
        state[int(user_id)]["membership"].add(int(book_id))
    return state


def _visible_book_scopes_for_owners(owners, session, candidates_by_owner):
    """Resolve supplied owners' candidate books in one live metadata query."""
    owners = [owner for owner in owners if owner is not None]
    if not owners:
        log.error("annotations: no device owners available; filtered views denied")
        raise FilteredBookVisibilityUnavailable("device owners unavailable")
    owner_ids = {int(owner.id) for owner in owners}
    if owner_ids != {int(owner_id) for owner_id in candidates_by_owner}:
        log.error("annotations: device owner/candidate scope mismatch; filtered views denied")
        raise FilteredBookVisibilityUnavailable("device owner scope mismatch")
    visibility_state = _owner_visibility_state(session, candidates_by_owner)
    resolved = calibre_db.get_filtered_book_ids_for_users(
        owners, visibility_state, candidates_by_owner,
        allow_show_global=True,
    )
    return {
        int(owner.id): frozenset(resolved.get(int(owner.id), ()))
        for owner in owners
    }


def _visible_book_scope_for_owner(owner, session, candidate_ids):
    if owner is None:
        log.error("annotations: device owner unavailable; filtered scope denied")
        raise FilteredBookVisibilityUnavailable("device owner unavailable")
    return _visible_book_scopes_for_owners(
        [owner], session, {int(owner.id): frozenset(candidate_ids)},
    ).get(
        int(owner.id), frozenset(),
    )


def _device_book_candidates(devices, owners_by_id, session):
    """Collect only books that have data relevant to the bounded device page."""
    owner_ids = tuple(sorted(int(owner_id) for owner_id in owners_by_id))
    candidates = {owner_id: set() for owner_id in owner_ids}
    if not devices:
        return {owner_id: frozenset() for owner_id in owner_ids}
    device_ids = tuple(device.id for device in devices)

    annotation_books = select(
        ub.Annotation.user_id.label("owner_id"),
        ub.Annotation.book_id.label("book_id"),
    ).where(
        ub.Annotation.user_id.in_(owner_ids),
        ub.Annotation.hidden.isnot(True),
        or_(
            ub.Annotation.origin_device_id.in_(device_ids),
            ub.Annotation.assigned_device_id.in_(device_ids),
        ),
    )
    retired_assignment_books = select(
        ub.Annotation.user_id.label("owner_id"),
        ub.Annotation.book_id.label("book_id"),
    ).select_from(ub.DeviceRetiredAssignment).join(
        ub.Annotation,
        ub.Annotation.id == ub.DeviceRetiredAssignment.annotation_id,
    ).where(
        ub.DeviceRetiredAssignment.device_id.in_(device_ids),
        ub.Annotation.user_id.in_(owner_ids),
        ub.Annotation.hidden.isnot(True),
    )
    position_books = select(
        ub.Device.user_id.label("owner_id"),
        ub.DeviceReadingPosition.book_id.label("book_id"),
    ).select_from(ub.DeviceReadingPosition).join(
        ub.Device, ub.Device.id == ub.DeviceReadingPosition.device_id,
    ).where(
        ub.DeviceReadingPosition.device_id.in_(device_ids),
        ub.Device.user_id.in_(owner_ids),
    )
    authority_books = select(
        ub.KoboAnnotationBookState.user_id.label("owner_id"),
        ub.KoboAnnotationBookState.book_id.label("book_id"),
    ).where(ub.KoboAnnotationBookState.user_id.in_(owner_ids))
    latest_report_ids = select(
        func.max(ub.DeviceInventoryReport.id),
    ).where(
        ub.DeviceInventoryReport.device_id.in_(device_ids),
    ).group_by(ub.DeviceInventoryReport.device_id)
    inventory_books = select(
        ub.Device.user_id.label("owner_id"),
        ub.DeviceInventoryItem.book_id.label("book_id"),
    ).select_from(ub.DeviceInventoryItem).join(
        ub.Device, ub.Device.id == ub.DeviceInventoryItem.device_id,
    ).where(
        ub.DeviceInventoryItem.device_id.in_(device_ids),
        ub.DeviceInventoryItem.last_report_id.in_(latest_report_ids),
        ub.DeviceInventoryItem.book_id.isnot(None),
        ub.Device.user_id.in_(owner_ids),
    )
    candidate_rows = union_all(
        annotation_books, retired_assignment_books, position_books,
        authority_books, inventory_books,
    ).subquery("device_book_candidates")
    rows = session.execute(select(
        candidate_rows.c.owner_id,
        candidate_rows.c.book_id,
    ).group_by(
        candidate_rows.c.owner_id,
        candidate_rows.c.book_id,
    )).all()
    for owner_id, book_id in rows:
        candidates[int(owner_id)].add(int(book_id))
    return {
        owner_id: frozenset(book_ids)
        for owner_id, book_ids in candidates.items()
    }


def _device_visibility_scopes(devices, owners_by_id, session):
    """Resolve the live owner view for the bounded devices, failing closed loudly."""
    if not devices:
        return {}
    expected_owner_ids = {int(device.user_id) for device in devices}
    if expected_owner_ids != set(owners_by_id):
        log.error("annotations: one or more device owners unavailable; filtered views denied")
        raise FilteredBookVisibilityUnavailable("device owner unavailable")
    candidates = _device_book_candidates(devices, owners_by_id, session)
    return _visible_book_scopes_for_owners(
        owners_by_id.values(), session, candidates,
    )


def _annotation_class_counts(rows):
    counts = {kind: 0 for kind in DEVICE_ANNOTATION_TYPES}
    for row in rows:
        if row.annotation_type in counts:
            counts[row.annotation_type] += 1
    return counts


def _device_annotation_row(row, books, device_public_ids):
    book = books[row.book_id]
    return {
        "annotation_id": row.annotation_id,
        "book_id": row.book_id,
        "annotation_type": row.annotation_type,
        "highlighted_text": row.highlighted_text,
        "highlight_color": to_display_name(row.highlight_color),
        "note_text": row.note_text,
        "chapter_progress": row.chapter_progress,
        "source": row.source,
        "position_type": row.position_type,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "client_modified_at": (
            row.client_modified_at.isoformat() if row.client_modified_at else None
        ),
        "server_modified_at": (
            row.server_modified_at.isoformat() if row.server_modified_at else None
        ),
        "origin_device_id": device_public_ids.get(row.origin_device_id),
        "assigned_device_id": device_public_ids.get(row.assigned_device_id),
        "book": {"id": row.book_id, "title": getattr(book, "title", None)},
    }


def _aggregate_device_rows(*, devices, owners_by_id, scopes, session):
    """Build bounded device cards with one grouped execution per data family."""
    if not devices:
        return []
    device_ids = tuple(device.id for device in devices)
    owner_ids = tuple(sorted(owners_by_id))
    device_id_set = set(device_ids)

    assigned_counts = {device_id: 0 for device_id in device_ids}
    origin_counts = {
        device_id: {kind: 0 for kind in DEVICE_ANNOTATION_TYPES}
        for device_id in device_ids
    }
    annotation_groups = session.query(
        ub.Annotation.origin_device_id,
        ub.Annotation.assigned_device_id,
        ub.Annotation.annotation_type,
        func.count(ub.Annotation.id),
    ).filter(
        ub.Annotation.hidden.isnot(True),
        or_(
            ub.Annotation.origin_device_id.in_(device_ids),
            ub.Annotation.assigned_device_id.in_(device_ids),
        ),
        _owner_book_in_scope(
            ub.Annotation.user_id, ub.Annotation.book_id, scopes,
        ),
    ).group_by(
        ub.Annotation.origin_device_id,
        ub.Annotation.assigned_device_id,
        ub.Annotation.annotation_type,
    ).all()
    for origin_id, assigned_id, annotation_type, count in annotation_groups:
        if assigned_id in assigned_counts:
            assigned_counts[assigned_id] += int(count)
        if origin_id in origin_counts and annotation_type in DEVICE_ANNOTATION_TYPES:
            origin_counts[origin_id][annotation_type] += int(count)

    position_counts = {}
    for device_id, book_count, last_position in session.query(
            ub.DeviceReadingPosition.device_id,
            func.count(func.distinct(ub.DeviceReadingPosition.book_id)),
            func.max(ub.DeviceReadingPosition.server_modified_at),
        ).filter(
            ub.DeviceReadingPosition.device_id.in_(device_ids),
            _device_book_in_scope(
                ub.DeviceReadingPosition.device_id,
                ub.DeviceReadingPosition.book_id,
                devices,
                scopes,
            ),
        ).group_by(ub.DeviceReadingPosition.device_id).all():
        position_counts[device_id] = (int(book_count), last_position)

    authority_by_owner = {
        owner_id: _empty_authority_rollup() for owner_id in owner_ids
    }
    total_authority_by_owner = {owner_id: 0 for owner_id in owner_ids}
    seeded_by_device = {device_id: 0 for device_id in device_ids}

    authority_status = select(
        literal("authority").label("metric"),
        ub.KoboAnnotationBookState.user_id.label("metric_key"),
        ub.KoboAnnotationBookState.authority_status.label("detail"),
        func.count(ub.KoboAnnotationBookState.id).label("value"),
    ).where(
        ub.KoboAnnotationBookState.user_id.in_(owner_ids),
        _owner_book_in_scope(
            ub.KoboAnnotationBookState.user_id,
            ub.KoboAnnotationBookState.book_id,
            scopes,
        ),
    ).group_by(
        ub.KoboAnnotationBookState.user_id,
        ub.KoboAnnotationBookState.authority_status,
    )

    active_counts = select(
        ub.Device.user_id.label("owner_id"),
        func.count(ub.Device.id).label("active_count"),
    ).where(
        ub.Device.user_id.in_(owner_ids),
        ub.Device.kind == "kobo",
        ub.Device.active.is_(True),
    ).group_by(ub.Device.user_id).subquery("active_kobo_counts")

    accepted_per_state = select(
        ub.KoboAnnotationBookState.id.label("state_id"),
        ub.KoboAnnotationBookState.user_id.label("owner_id"),
        func.count(func.distinct(ub.KoboAnnotationSeedCapture.device_id)).label(
            "accepted_count",
        ),
    ).select_from(ub.KoboAnnotationBookState).join(
        ub.KoboAnnotationSeedCapture,
        ub.KoboAnnotationSeedCapture.book_state_id == ub.KoboAnnotationBookState.id,
    ).join(
        ub.Device,
        ub.Device.id == ub.KoboAnnotationSeedCapture.device_id,
    ).where(
        ub.KoboAnnotationBookState.user_id.in_(owner_ids),
        ub.Device.kind == "kobo",
        ub.Device.active.is_(True),
        ub.KoboAnnotationSeedCapture.result == "accepted",
        ub.KoboAnnotationSeedCapture.completed_at.isnot(None),
        _owner_book_in_scope(
            ub.KoboAnnotationBookState.user_id,
            ub.KoboAnnotationBookState.book_id,
            scopes,
        ),
    ).group_by(
        ub.KoboAnnotationBookState.id,
        ub.KoboAnnotationBookState.user_id,
    ).subquery("accepted_seed_coverage")

    partial_coverage = select(
        literal("partial").label("metric"),
        accepted_per_state.c.owner_id.label("metric_key"),
        literal(None).label("detail"),
        func.count(accepted_per_state.c.state_id).label("value"),
    ).select_from(accepted_per_state).join(
        active_counts,
        active_counts.c.owner_id == accepted_per_state.c.owner_id,
    ).where(
        accepted_per_state.c.accepted_count > 0,
        accepted_per_state.c.accepted_count < active_counts.c.active_count,
    ).group_by(accepted_per_state.c.owner_id)

    seeded_devices = select(
        literal("seeded").label("metric"),
        ub.KoboAnnotationSeedCapture.device_id.label("metric_key"),
        literal(None).label("detail"),
        func.count(func.distinct(ub.KoboAnnotationBookState.id)).label("value"),
    ).select_from(ub.KoboAnnotationSeedCapture).join(
        ub.KoboAnnotationBookState,
        ub.KoboAnnotationBookState.id == ub.KoboAnnotationSeedCapture.book_state_id,
    ).join(
        ub.Device,
        ub.Device.id == ub.KoboAnnotationSeedCapture.device_id,
    ).where(
        ub.KoboAnnotationSeedCapture.device_id.in_(device_ids),
        ub.Device.active.is_(True),
        ub.Device.kind == "kobo",
        ub.KoboAnnotationSeedCapture.result == "accepted",
        ub.KoboAnnotationSeedCapture.completed_at.isnot(None),
        _owner_book_in_scope(
            ub.KoboAnnotationBookState.user_id,
            ub.KoboAnnotationBookState.book_id,
            scopes,
        ),
    ).group_by(ub.KoboAnnotationSeedCapture.device_id)

    authority_metrics = session.execute(union_all(
        authority_status, partial_coverage, seeded_devices,
    )).all()
    for metric, metric_key, detail, value in authority_metrics:
        metric_key = int(metric_key)
        value = int(value)
        if metric == "authority" and detail in AUTHORITY_STATUSES:
            authority_by_owner[metric_key][detail] = value
            total_authority_by_owner[metric_key] += value
        elif metric == "partial":
            authority_by_owner[metric_key]["books_partially_seeded"] = value
        elif metric == "seeded" and metric_key in device_id_set:
            seeded_by_device[metric_key] = value

    latest_report_ids = session.query(
        func.max(ub.DeviceInventoryReport.id),
    ).filter(
        ub.DeviceInventoryReport.device_id.in_(device_ids),
    ).group_by(ub.DeviceInventoryReport.device_id)
    reports = {
        report.device_id: report
        for report in session.query(ub.DeviceInventoryReport).filter(
            ub.DeviceInventoryReport.id.in_(latest_report_ids),
        ).all()
    }
    report_ids = tuple(report.id for report in reports.values())
    inventory_counts = {device_id: 0 for device_id in device_ids}
    if report_ids:
        for device_id, count in session.query(
                ub.DeviceInventoryItem.device_id,
                func.count(ub.DeviceInventoryItem.id),
            ).filter(
                ub.DeviceInventoryItem.device_id.in_(device_ids),
                ub.DeviceInventoryItem.last_report_id.in_(report_ids),
                or_(
                    ub.DeviceInventoryItem.book_id.is_(None),
                    _device_book_in_scope(
                        ub.DeviceInventoryItem.device_id,
                        ub.DeviceInventoryItem.book_id,
                        devices,
                        scopes,
                    ),
                ),
            ).group_by(ub.DeviceInventoryItem.device_id).all():
            inventory_counts[device_id] = int(count)

    latest_storage_ids = session.query(
        func.max(ub.DeviceStorageSnapshot.id),
    ).filter(
        ub.DeviceStorageSnapshot.device_id.in_(device_ids),
    ).group_by(ub.DeviceStorageSnapshot.device_id)
    storage = {
        snapshot.device_id: snapshot
        for snapshot in session.query(ub.DeviceStorageSnapshot).filter(
            ub.DeviceStorageSnapshot.id.in_(latest_storage_ids),
        ).all()
    }

    rows = []
    for device in devices:
        owner_id = int(device.user_id)
        seeded = (
            seeded_by_device[device.id]
            if device.kind == "kobo" and device.active else 0
        )
        unseeded = (
            max(total_authority_by_owner.get(owner_id, 0) - seeded, 0)
            if device.kind == "kobo" and device.active else 0
        )
        books_with_position, last_position = position_counts.get(device.id, (0, None))
        rows.append(_device_json(
            device,
            assigned_counts.get(device.id, 0),
            reports.get(device.id),
            storage.get(device.id),
            annotation_counts=origin_counts.get(device.id),
            authority_rollup=authority_by_owner.get(owner_id),
            seeded_books=seeded,
            unseeded_books=unseeded,
            inventory_count=inventory_counts.get(device.id, 0),
            books_with_position=books_with_position,
            last_position_at=last_position,
        ))
    return rows


def list_annotation_devices(*, user_id, session, active_only=False,
                            limit=DEFAULT_DEVICE_LIST_LIMIT, offset=0,
                            return_total=False):
    """List one bounded page with SQL-aggregated, owner-filtered counts."""
    query = session.query(ub.Device).filter(ub.Device.user_id == user_id)
    if active_only:
        query = query.filter(ub.Device.active.is_(True))
    total = query.count()
    devices = query.order_by(ub.Device.display_name, ub.Device.id).offset(
        offset,
    ).limit(min(limit, MAX_DEVICE_LIST_LIMIT)).all()
    owner = session.query(ub.User).filter(ub.User.id == user_id).first()
    owners_by_id = {} if owner is None else {int(owner.id): owner}
    scopes = _device_visibility_scopes(devices, owners_by_id, session)
    rows = _aggregate_device_rows(
        devices=devices,
        owners_by_id=owners_by_id,
        scopes=scopes,
        session=session,
    )
    return (rows, total) if return_total else rows


def rename_annotation_device(public_id, *, user_id, label, session, commit):
    if not isinstance(label, str) or label != label.strip() or not 1 <= len(label) <= 60:
        raise ValueError("device label must be 1-60 characters without surrounding whitespace")
    if any(ord(char) < 32 or ord(char) == 127 for char in label):
        raise ValueError("device label contains control characters")
    device = _owned_device(public_id, user_id, session)
    if device is None:
        return None
    device.display_name = label
    _commit_required(commit)
    return device


def device_annotation_counts(public_id, *, user_id, session):
    device = _owned_device(public_id, user_id, session)
    if device is None:
        return None
    owner = _device_owner(device, session)
    visible_ids = _device_visibility_scopes(
        [device], {} if owner is None else {int(owner.id): owner}, session,
    ).get(int(device.user_id), frozenset())
    origin, assigned = session.query(
        func.sum(case((ub.Annotation.origin_device_id == device.id, 1), else_=0)),
        func.sum(case((ub.Annotation.assigned_device_id == device.id, 1), else_=0)),
    ).filter(
        ub.Annotation.user_id == user_id,
        ub.Annotation.hidden.isnot(True),
        _book_in_scope(ub.Annotation.book_id, visible_ids),
        or_(
            ub.Annotation.origin_device_id == device.id,
            ub.Annotation.assigned_device_id == device.id,
        ),
    ).one()
    return device, {
        "origin_count": int(origin or 0),
        "assigned_count": int(assigned or 0),
    }


def soft_delete_annotation_device(public_id, *, user_id, session, commit):
    found = device_annotation_counts(public_id, user_id=user_id, session=session)
    if found is None:
        return None
    device, counts = found
    assigned = session.query(ub.Annotation).filter(
        ub.Annotation.user_id == user_id, ub.Annotation.assigned_device_id == device.id,
    ).yield_per(200)
    for annotation in assigned:
        snapshot = session.query(ub.DeviceRetiredAssignment).filter_by(
            device_id=device.id, annotation_id=annotation.id,
        ).first()
        if snapshot is None:
            session.add(ub.DeviceRetiredAssignment(device_id=device.id, annotation_id=annotation.id))
        annotation.assigned_device_id = None
        annotation.routing_revision = (annotation.routing_revision or 0) + 1
    session.query(ub.AnnotationDeviceState).filter_by(device_id=device.id).update(
        {ub.AnnotationDeviceState.desired: False}, synchronize_session=False,
    )
    device.active = False
    _commit_required(commit)
    return device, counts


def restore_annotation_device(public_id, *, user_id, session, commit):
    device = _owned_device(public_id, user_id, session)
    if device is None:
        return None
    restored = 0
    conflicts = 0
    owner = _device_owner(device, session)
    visible_ids = _device_visibility_scopes(
        [device], {} if owner is None else {int(owner.id): owner}, session,
    ).get(int(device.user_id), frozenset())
    snapshots = session.query(
        ub.DeviceRetiredAssignment,
        ub.Annotation,
        ub.AnnotationDeviceState,
    ).join(
        ub.Annotation,
        and_(
            ub.Annotation.id == ub.DeviceRetiredAssignment.annotation_id,
            ub.Annotation.user_id == user_id,
        ),
    ).outerjoin(
        ub.AnnotationDeviceState,
        and_(
            ub.AnnotationDeviceState.annotation_id == ub.Annotation.id,
            ub.AnnotationDeviceState.device_id == device.id,
        ),
    ).filter(
        ub.DeviceRetiredAssignment.device_id == device.id,
        ub.Annotation.hidden.isnot(True),
        _book_in_scope(ub.Annotation.book_id, visible_ids),
    ).yield_per(200)
    for snapshot, annotation, state in snapshots:
        if annotation is not None and annotation.assigned_device_id is None:
            annotation.assigned_device_id = device.id
            annotation.routing_revision = (annotation.routing_revision or 0) + 1
            if state is None:
                state = ub.AnnotationDeviceState(annotation_id=annotation.id, device_id=device.id)
                session.add(state)
            state.desired = True
            restored += 1
        elif annotation is not None:
            conflicts += 1
        session.delete(snapshot)
    device.active = True
    _commit_required(commit)
    return device, restored, conflicts


@annotations_bp.route("/api/annotations/devices", methods=["GET"])
@user_login_required
def annotation_devices_list():
    active_only = request.args.get("active", "").lower() == "true"
    try:
        pagination, error_response, error_status = _offset_pagination(
            default_limit=DEFAULT_DEVICE_LIST_LIMIT,
            max_limit=MAX_DEVICE_LIST_LIMIT,
        )
        if error_response is not None:
            return error_response, error_status
        limit, offset = pagination
        devices, total = list_annotation_devices(
            user_id=current_user.id, session=ub.session, active_only=active_only,
            limit=limit, offset=offset, return_total=True,
        )
    except FilteredBookVisibilityUnavailable as error:
        return _visibility_unavailable_response("device list", error)
    except (RuntimeError, SQLAlchemyError):
        return _database_error_response("device list")
    return jsonify({
        "devices": devices,
        "limit": limit,
        "offset": offset,
        "total": total,
    })


def _device_annotation_filters():
    role = request.args.get("role", "origin").strip().lower()
    assigned_toggle = request.args.get("assigned")
    if assigned_toggle is not None:
        toggle = assigned_toggle.strip().lower()
        if toggle not in ("true", "false"):
            return None, jsonify({
                "error": "invalid_filter", "field": "assigned",
                "message": "assigned must be true or false",
            }), 400
        role = "assigned" if toggle == "true" else "origin"
    if role not in DEVICE_ANNOTATION_ROLES:
        return None, jsonify({
            "error": "invalid_filter", "field": "role",
            "message": "role must be origin or assigned",
        }), 400

    annotation_type = request.args.get("type")
    if annotation_type is not None:
        annotation_type = annotation_type.strip().lower()
        if annotation_type not in DEVICE_ANNOTATION_TYPES:
            return None, jsonify({
                "error": "invalid_filter", "field": "type",
                "message": "type must be highlight, note, or dogear",
            }), 400

    raw_book_id = request.args.get("book_id")
    try:
        book_id = None if raw_book_id is None else int(raw_book_id)
    except (TypeError, ValueError):
        book_id = 0
    if book_id is not None and book_id < 1:
        return None, jsonify({
            "error": "invalid_filter", "field": "book_id",
            "message": "book_id must be a positive integer",
        }), 400

    raw_page = request.args.get("page")
    try:
        page = 1 if raw_page is None else int(raw_page)
    except (TypeError, ValueError):
        page = 0
    if not 1 <= page <= MAX_DEVICE_ANNOTATION_PAGE:
        return None, jsonify({
            "error": "invalid_pagination", "field": "page",
            "message": f"page must be between 1 and {MAX_DEVICE_ANNOTATION_PAGE}",
            "max_page": MAX_DEVICE_ANNOTATION_PAGE,
        }), 400
    return {
        "role": role, "annotation_type": annotation_type,
        "book_id": book_id, "page": page,
    }, None, None


@annotations_bp.route(
    "/api/annotations/devices/<public_id>/annotations", methods=["GET"],
)
@user_login_required
def annotation_device_annotations(public_id):
    """Return one bounded page from an owned device's live filtered view."""
    try:
        device = _owned_device(public_id, current_user.id, ub.session)
        if device is None:
            abort(404)
        filters, error_response, error_status = _device_annotation_filters()
        if error_response is not None:
            return error_response, error_status
        owner = _device_owner(device, ub.session)
        scope_column = (
            ub.Annotation.assigned_device_id
            if filters["role"] == "assigned"
            else ub.Annotation.origin_device_id
        )
        query = ub.session.query(ub.Annotation).filter(
            ub.Annotation.user_id == device.user_id,
            scope_column == device.id,
            ub.Annotation.hidden.isnot(True),
        )
        if filters["book_id"] is not None:
            query = query.filter(ub.Annotation.book_id == filters["book_id"])
        if filters["annotation_type"] is not None:
            query = query.filter(
                ub.Annotation.annotation_type == filters["annotation_type"],
            )
        visible_ids = _device_visibility_scopes(
            [device], {} if owner is None else {int(owner.id): owner}, ub.session,
        ).get(int(device.user_id), frozenset())
        query = query.filter(_book_in_scope(ub.Annotation.book_id, visible_ids))
        facet_rows = query.with_entities(
            ub.Annotation.annotation_type,
            func.count(ub.Annotation.id),
        ).group_by(ub.Annotation.annotation_type).all()
        annotation_counts = {kind: 0 for kind in DEVICE_ANNOTATION_TYPES}
        for annotation_type, count in facet_rows:
            if annotation_type in annotation_counts:
                annotation_counts[annotation_type] = int(count)
        total = sum(int(count) for _annotation_type, count in facet_rows)
        offset = (filters["page"] - 1) * DEVICE_ANNOTATION_PAGE_SIZE
        rows = query.order_by(
            ub.Annotation.server_modified_at.desc(),
            ub.Annotation.created_at.desc(),
            ub.Annotation.id.desc(),
        ).offset(offset).limit(DEVICE_ANNOTATION_PAGE_SIZE).all()
        books = _visible_books_for_owner(owner, (row.book_id for row in rows))
        rows = [row for row in rows if row.book_id in books]
        referenced_device_ids = {device.id}
        for row in rows:
            if row.origin_device_id is not None:
                referenced_device_ids.add(row.origin_device_id)
            if row.assigned_device_id is not None:
                referenced_device_ids.add(row.assigned_device_id)
        device_public_ids, devices = _annotation_device_payload(
            device.user_id, ub.session, device_ids=referenced_device_ids,
        )
        device_payload = _aggregate_device_rows(
            devices=[device],
            owners_by_id={} if owner is None else {int(owner.id): owner},
            scopes={} if owner is None else {int(owner.id): visible_ids},
            session=ub.session,
        )[0]
        device_payload.update({
            "highlights": annotation_counts["highlight"],
            "notes": annotation_counts["note"],
            "dogears": annotation_counts["dogear"],
        })
        return jsonify({
            "device": device_payload,
            "annotations": [
                _device_annotation_row(row, books, device_public_ids) for row in rows
            ],
            "devices": devices,
            "books": {
                str(book_id): {"id": book_id, "title": getattr(book, "title", None)}
                for book_id, book in books.items()
                if any(row.book_id == book_id for row in rows)
            },
            "role": filters["role"],
            "type": filters["annotation_type"],
            "book_id": filters["book_id"],
            "page": filters["page"],
            "page_size": DEVICE_ANNOTATION_PAGE_SIZE,
            "pages": (total + DEVICE_ANNOTATION_PAGE_SIZE - 1) // DEVICE_ANNOTATION_PAGE_SIZE,
            "total": total,
        })
    except FilteredBookVisibilityUnavailable as error:
        return _visibility_unavailable_response("device annotations", error)
    except SQLAlchemyError:
        return _database_error_response("device annotations")


@annotations_bp.route(
    "/api/annotations/devices/<public_id>/summary", methods=["GET"],
)
@user_login_required
def annotation_device_summary(public_id):
    """Return visible per-class, position, and seed counts for one device."""
    try:
        device = _owned_device(public_id, current_user.id, ub.session)
        if device is None:
            abort(404)
        owner = _device_owner(device, ub.session)
        visible_ids = _device_visibility_scopes(
            [device], {} if owner is None else {int(owner.id): owner}, ub.session,
        ).get(int(device.user_id), frozenset())
        aggregate = _aggregate_device_rows(
            devices=[device],
            owners_by_id={} if owner is None else {int(owner.id): owner},
            scopes={} if owner is None else {int(owner.id): visible_ids},
            session=ub.session,
        )[0]
        return jsonify({
            "highlights": aggregate["highlights"],
            "notes": aggregate["notes"],
            "dogears": aggregate["dogears"],
            "books_with_position": aggregate["books_with_position"],
            "last_position_at": aggregate["last_position_at"],
            "seeded_books": aggregate["seeded_books"],
            "unseeded_books": aggregate["unseeded_books"],
        })
    except FilteredBookVisibilityUnavailable as error:
        return _visibility_unavailable_response("device summary", error)
    except SQLAlchemyError:
        return _database_error_response("device summary")


@annotations_bp.route(
    "/api/annotations/devices/<public_id>/positions", methods=["GET"],
)
@user_login_required
def annotation_device_positions(public_id):
    """Return visible per-book position rows for one owned device."""
    try:
        device = _owned_device(public_id, current_user.id, ub.session)
        if device is None:
            abort(404)
        pagination, error_response, error_status = _offset_pagination(
            default_limit=DEFAULT_DEVICE_POSITION_LIMIT,
            max_limit=MAX_DEVICE_POSITION_LIMIT,
        )
        if error_response is not None:
            return error_response, error_status
        limit, offset = pagination
        owner = _device_owner(device, ub.session)
        visible_ids = _device_visibility_scopes(
            [device], {} if owner is None else {int(owner.id): owner}, ub.session,
        ).get(int(device.user_id), frozenset())
        query = ub.session.query(ub.DeviceReadingPosition).filter(
            ub.DeviceReadingPosition.device_id == device.id,
            _book_in_scope(ub.DeviceReadingPosition.book_id, visible_ids),
        )
        total = query.count()
        rows = query.order_by(
            ub.DeviceReadingPosition.server_modified_at.desc(),
            ub.DeviceReadingPosition.book_id,
        ).offset(offset).limit(limit).all()
        books = _visible_books_for_owner(owner, (row.book_id for row in rows))
        rows = [row for row in rows if row.book_id in books]
        return jsonify({
            "device": _device_json(device),
            "positions": [{
                "book_id": row.book_id,
                "book": {
                    "id": row.book_id,
                    "title": getattr(books[row.book_id], "title", None),
                },
                "location_source": row.location_source,
                "location_type": row.location_type,
                "location_value": row.location_value,
                "progress_percent": row.progress_percent,
                "content_source_progress_percent": row.content_source_progress_percent,
                "cfi": row.cfi,
                "client_modified_at": (
                    row.client_modified_at.isoformat() if row.client_modified_at else None
                ),
                "server_modified_at": row.server_modified_at.isoformat(),
                "rehydrate_needed": bool(row.rehydrate_needed),
            } for row in rows],
            "books": {
                str(book_id): {"id": book_id, "title": getattr(book, "title", None)}
                for book_id, book in books.items()
            },
            "limit": limit,
            "offset": offset,
            "total": total,
        })
    except FilteredBookVisibilityUnavailable as error:
        return _visibility_unavailable_response("device positions", error)
    except SQLAlchemyError:
        return _database_error_response("device positions")


@annotations_bp.route("/api/admin/devices", methods=["GET"])
@admin_required
def annotation_admin_devices():
    """Return the cross-user device board without opaque identity material."""
    try:
        pagination, error_response, error_status = _offset_pagination(
            default_limit=DEFAULT_ADMIN_DEVICE_LIMIT,
            max_limit=MAX_ADMIN_DEVICE_LIMIT,
        )
        if error_response is not None:
            return error_response, error_status
        limit, offset = pagination
        total = ub.session.query(func.count(ub.Device.id)).scalar() or 0
        page = ub.session.query(ub.Device, ub.User).outerjoin(
            ub.User, ub.User.id == ub.Device.user_id,
        ).order_by(
            ub.User.name, ub.User.id, ub.Device.display_name, ub.Device.id,
        ).offset(offset).limit(limit).all()
        devices = [device for device, _owner in page]
        owners_by_id = {
            int(owner.id): owner for _device, owner in page if owner is not None
        }
        scopes = _device_visibility_scopes(devices, owners_by_id, ub.session)
        aggregates = _aggregate_device_rows(
            devices=devices,
            owners_by_id=owners_by_id,
            scopes=scopes,
            session=ub.session,
        )
        rows = [{
            **aggregate,
            "user": {
                "id": owners_by_id[int(device.user_id)].id,
                "name": owners_by_id[int(device.user_id)].name,
            },
        } for device, aggregate in zip(devices, aggregates)]
        return jsonify({
            "devices": rows,
            "limit": limit,
            "offset": offset,
            "total": int(total),
        })
    except FilteredBookVisibilityUnavailable as error:
        return _visibility_unavailable_response("admin device board", error)
    except (RuntimeError, SQLAlchemyError):
        return _database_error_response("admin device board")


@annotations_bp.route("/api/annotations/devices/<public_id>/inventory", methods=["GET"])
@user_login_required
def annotation_device_inventory(public_id):
    """Return one bounded page of the latest inventory for an owned device."""
    try:
        device = _owned_device(public_id, current_user.id, ub.session)
        if device is None:
            abort(404)
        pagination, error_response, error_status = _device_inventory_pagination()
        if error_response is not None:
            return error_response, error_status
        limit, offset = pagination
        owner = _device_owner(device, ub.session)
        visible_ids = _device_visibility_scopes(
            [device], {} if owner is None else {int(owner.id): owner}, ub.session,
        ).get(int(device.user_id), frozenset())
        report = (
            ub.session.query(ub.DeviceInventoryReport)
            .filter_by(device_id=device.id)
            .order_by(ub.DeviceInventoryReport.id.desc())
            .first()
        )
        storage = (
            ub.session.query(ub.DeviceStorageSnapshot)
            .filter_by(device_id=device.id)
            .order_by(ub.DeviceStorageSnapshot.id.desc())
            .first()
        )
        if report is None:
            return jsonify({
                # Both halves are load-bearing: the storage snapshot (Phase 3)
                # and the pagination envelope (F3). A caller paging this endpoint
                # gets the same shape whether or not a report exists.
                "device": _device_json(
                    device, storage_snapshot=storage, inventory_count=0,
                ),
                "observed_at": None,
                "books": [],
                "limit": limit,
                "offset": offset,
                "total": 0,
            })
        items_query = (
            ub.session.query(ub.DeviceInventoryItem)
            .filter_by(device_id=device.id, last_report_id=report.id)
            .filter(
                or_(
                    ub.DeviceInventoryItem.book_id.is_(None),
                    _book_in_scope(ub.DeviceInventoryItem.book_id, visible_ids),
                ),
            )
        )
        total = items_query.count()
        items = []
        if offset < total:
            items = (
                items_query
                .order_by(ub.DeviceInventoryItem.lpath, ub.DeviceInventoryItem.id)
                .offset(offset)
                .limit(limit)
                .all()
            )
        books = _visible_books_for_owner(owner, (item.book_id for item in items))
        items = [
            item for item in items
            if item.book_id is None or item.book_id in books
        ]
        return jsonify({
            "device": _device_json(
                device, inventory_report=report, storage_snapshot=storage,
                inventory_count=total,
            ),
            "observed_at": report.observed_at.isoformat() if report.observed_at else None,
            "books": [{
                "inventory_item_id": item.id,
                "book_id": item.book_id,
                "lpath": item.lpath,
                "checksum": item.checksum,
                "size": item.size,
                "mtime": item.mtime,
            } for item in items],
            "limit": limit,
            "offset": offset,
            "total": total,
        })
    except FilteredBookVisibilityUnavailable as error:
        return _visibility_unavailable_response("device inventory", error)
    except SQLAlchemyError:
        return _database_error_response("device inventory")


@annotations_bp.route(
    "/api/annotations/devices/<public_id>/inventory/<int:item_id>/delete",
    methods=["POST"],
)
@user_login_required
def annotation_device_inventory_delete(public_id, item_id):
    """Queue deletion of one exact observed path; omissions cannot reach here."""
    try:
        item_and_device = ub.session.query(
            ub.DeviceInventoryItem, ub.Device,
        ).join(
            ub.Device, ub.Device.id == ub.DeviceInventoryItem.device_id,
        ).filter(
            ub.Device.public_id == public_id,
            ub.Device.user_id == current_user.id,
            ub.Device.active.is_(True),
            ub.DeviceInventoryItem.id == item_id,
        ).one_or_none()
        if item_and_device is None:
            raise device_capabilities.CapabilityValidationError()
        item, device = item_and_device
        owner = _device_owner(device, ub.session)
        candidate_ids = [] if item.book_id is None else [item.book_id]
        visible_ids = _visible_book_scope_for_owner(
            owner, ub.session, candidate_ids,
        )
        if item.book_id is not None and (
                item.book_id not in visible_ids
                or calibre_db.get_filtered_book(item.book_id, user=owner) is None):
            raise device_capabilities.CapabilityValidationError()
        deletion = device_capabilities.queue_named_deletion(
            session=ub.session, user_id=current_user.id,
            device_public_id=public_id, inventory_item_id=item_id,
        )
        _commit_required(ub.session_commit)
        return jsonify({
            "deletion_id": deletion.id,
            "lpath": deletion.lpath,
            "state": deletion.state,
        }), 202
    except device_capabilities.CapabilityValidationError:
        ub.session.rollback()
        return jsonify({"error": "inventory_item_not_found"}), 404
    except FilteredBookVisibilityUnavailable as error:
        return _visibility_unavailable_response(
            "device inventory deletion request", error,
        )
    except (RuntimeError, SQLAlchemyError):
        return _database_error_response("device inventory deletion request")


@annotations_bp.route("/api/annotations/devices/<public_id>", methods=["PATCH"])
@user_login_required
def annotation_device_rename(public_id):
    data = request.get_json(silent=True) or {}
    try:
        device = rename_annotation_device(
            public_id, user_id=current_user.id, label=data.get("label"),
            session=ub.session, commit=ub.session_commit,
        )
    except ValueError as error:
        return jsonify({"error": "invalid_label", "message": str(error)}), 400
    except (RuntimeError, SQLAlchemyError):
        return _database_error_response("device rename")
    if device is None:
        abort(404)
    return jsonify(_device_json(device))


@annotations_bp.route("/api/annotations/devices/<public_id>/delete-preflight", methods=["GET"])
@user_login_required
def annotation_device_delete_preflight(public_id):
    try:
        found = device_annotation_counts(public_id, user_id=current_user.id, session=ub.session)
    except FilteredBookVisibilityUnavailable as error:
        return _visibility_unavailable_response("device delete preflight", error)
    except (RuntimeError, SQLAlchemyError):
        return _database_error_response("device delete preflight")
    if found is None:
        abort(404)
    return jsonify(found[1])


@annotations_bp.route("/api/annotations/devices/<public_id>", methods=["DELETE"])
@user_login_required
def annotation_device_delete(public_id):
    try:
        result = soft_delete_annotation_device(
            public_id, user_id=current_user.id, session=ub.session, commit=ub.session_commit,
        )
    except FilteredBookVisibilityUnavailable as error:
        return _visibility_unavailable_response("device soft-delete", error)
    except (RuntimeError, SQLAlchemyError):
        return _database_error_response("device soft-delete")
    if result is None:
        abort(404)
    device, counts = result
    return jsonify({"device": _device_json(device), **counts})


@annotations_bp.route("/api/annotations/devices/<public_id>/restore", methods=["POST"])
@user_login_required
def annotation_device_restore(public_id):
    try:
        result = restore_annotation_device(
            public_id, user_id=current_user.id, session=ub.session, commit=ub.session_commit,
        )
    except FilteredBookVisibilityUnavailable as error:
        return _visibility_unavailable_response("device restore", error)
    except (RuntimeError, SQLAlchemyError):
        return _database_error_response("device restore")
    if result is None:
        abort(404)
    device, restored, conflicts = result
    return jsonify({"device": _device_json(device), "restored_assignment_count": restored,
                    "assignment_conflict_count": conflicts})


@annotations_bp.route("/annotations/import", methods=["GET"])
@user_login_required
def annotations_import_form():
    """Render the upload form."""
    return render_title_template(
        "annotations_import.html",
        title=_(u"Import Kobo annotations"),
        page="annotations_import",
    )


@annotations_bp.route("/annotations/import", methods=["POST"])
@user_login_required
def annotations_import_submit():
    """Accept an uploaded ``KoboReader.sqlite``, parse the Bookmark
    table, and merge each recoverable annotation into ``kobo_annotation_sync``
    for the current user.

    Returns a JSON summary with new/updated and reasoned skip counts; the
    upload form swaps to a result pane without a page reload.
    """
    try:
        with temporary_kobo_database(
                request.files.get("file"), request.content_length or 0) as tmp_path:
            summary = _ingest_bookmarks(tmp_path)
    except KoboUploadError as error:
        messages = {
            "no_file": _("No file uploaded."),
            "not_sqlite": _("Uploaded file is not a SQLite database."),
            "not_kobo": _("Uploaded database has no readable Kobo Bookmark table."),
            "too_large": _(
                "File exceeds %(max)d MB.",
                max=MAX_UPLOAD_BYTES // (1024 * 1024),
            ),
        }
        return jsonify({
            "error": error.code,
            "message": messages[error.code],
        }), error.status_code

    return jsonify(summary), 200


def _ingest_bookmarks(sqlite_path: str) -> dict:
    """Adapter around :func:`ingest_bookmarks` that pulls dependencies
    from the Flask request context (current_user, ub.session,
    calibre_db). Lives so the request handler is one line; the
    actual work happens in the pure function below."""
    origin_device_id = None
    supplied_device = request.form.get("origin_device_id")
    if supplied_device:
        try:
            from .services.device_registry import resolve_owned_device_best_effort
            origin_device_id = resolve_owned_device_best_effort(
                user_id=current_user.id, public_id=supplied_device,
            )
        except Exception:
            log.warning("annotations: imported-device attribution failed", exc_info=True)
    return ingest_bookmarks(
        sqlite_path,
        user_id=current_user.id,
        session=ub.session,
        book_lookup=lambda uuid: (
            calibre_db.get_book_by_uuid(uuid) if "-" in (uuid or "") else None
        ),
        commit=ub.session_commit,
        origin_device_id=origin_device_id,
    )


def _bookmark_has_recoverable_content(text, note, annotation_type) -> bool:
    """Whether a device row carries evidence of an annotation.

    Kobo stores highlights in ``Text``, attached/note-only writing in
    ``Annotation``, and dogears (whose text is empty) in ``Type``. Keep all
    three inputs explicit: reducing this to the historical ``bool(Text)`` gate
    makes dogears and note-only rows disappear again.
    """
    return any(
        isinstance(value, str) and bool(value.strip())
        for value in (text, note, annotation_type)
    )


def _parse_kobo_datetime(value, *, assume_naive_utc=False):
    """Parse a Kobo ISO-8601 clock into the DB's naive-UTC convention.

    Naive clocks are refused by default. Only the DateCreated import opts into
    the measured UTC convention; a naive DateModified must never gain overwrite
    authority by guessing which instant its device-local clock represented.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            if not assume_naive_utc:
                return None
            # DateCreated is descriptive: Kobo writes it without an offset even
            # though its paired DateModified uses ``Z``. On the measured device
            # all 31 pairs agreed to the second, so the DateCreated caller may
            # interpret it as UTC. This is strong evidence, not proof (one device,
            # one zone). DateModified is deliberately different: it decides
            # whether device content may overwrite a server edit, so an ambiguous
            # local clock must fail closed rather than be guessed into the future.
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError):
        return None


def _naive_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _device_edit_is_newer(device_modified_at, existing) -> bool:
    """Return whether an imported device edit may replace ``existing``.

    The device must supply a valid clock later than *every* clock recording an
    accepted state on the server. Comparing only to ``client_modified_at``
    would let an older snapshot overwrite a later server/web edit; comparing
    only to server receipt time would ignore the device's edit ordering. The
    maximum is deliberately conservative because recovery must prefer a
    reported conflict over silent loss of newer server state.
    """
    if device_modified_at is None:
        return False
    accepted_clocks = [
        _naive_utc(getattr(existing, name, None))
        for name in (
            "client_modified_at", "server_modified_at", "last_synced", "created_at",
        )
    ]
    watermark = max((clock for clock in accepted_clocks if clock is not None), default=None)
    return watermark is None or device_modified_at > watermark


def _matching_container_child_index(value):
    """Collapse the two equivalent KoboSpan selector sentinels for equality.

    Nickel persists ``-99`` in KoboReader.sqlite while its wire annotation omits
    the child-index fields, which stores as ``NULL``.  Both the position
    converter and the web-reader locator consume either spelling as "use the
    selector path", so the recovery comparator must not invent a content
    conflict from that structural transport difference.  See the measured wire
    behavior documented in ``cps/services/kobo_position.py:153``.
    """
    from .services.kobo_position import KOBO_SELECTOR_SENTINEL

    if value is None or value == KOBO_SELECTOR_SENTINEL:
        return None
    return value


def _imported_container_child_index(existing, imported):
    """Keep the stored spelling when an imported child index is equivalent.

    An authorised device edit may replace real content, but it should not turn
    a wire-written ``NULL`` into the equivalent device sentinel ``-99`` as a
    side effect.  A genuinely different child index still comes from the newer
    device row.
    """
    if (
        _matching_container_child_index(existing)
        == _matching_container_child_index(imported)
    ):
        return existing
    return imported


def _matching_annotation_values(values):
    """Return comparator-only values with equivalent child sentinels folded."""
    values = list(values)
    for index in (5, 8):
        values[index] = _matching_container_child_index(values[index])
    return tuple(values)


def _bookmark_values(bm, content_id):
    return (
        bm.text,
        bm.annotation,
        bm.color,
        content_id,
        bm.start_container_path,
        bm.start_container_child_index,
        bm.start_offset,
        bm.end_container_path,
        bm.end_container_child_index,
        bm.end_offset,
        bm.context_string,
        bm.chapter_progress,
        to_storage_type(bm.annotation_type),
    )


def _annotation_values(row):
    return (
        row.highlighted_text,
        row.note_text,
        to_storage_color(row.highlight_color),
        row.content_id,
        row.start_container_path,
        row.start_container_child_index,
        row.start_offset,
        row.end_container_path,
        row.end_container_child_index,
        row.end_offset,
        row.context_string,
        row.chapter_progress,
        to_storage_type(row.annotation_type),
    )


def _bookmark_matches_annotation(bm, content_id, row) -> bool:
    return (
        not bool(row.hidden)
        and _matching_annotation_values(_bookmark_values(bm, content_id))
        == _matching_annotation_values(_annotation_values(row))
    )


def _apply_imported_bookmark(row, bm, content_id, *, device_modified_at,
                             origin_device_id):
    """Apply the content half of an already-authorised newer device edit."""
    row.highlighted_text = bm.text
    row.note_text = bm.annotation
    row.highlight_color = bm.color
    row.content_id = content_id
    row.start_container_path = bm.start_container_path
    row.start_container_child_index = _imported_container_child_index(
        row.start_container_child_index, bm.start_container_child_index,
    )
    row.start_offset = bm.start_offset
    row.end_container_path = bm.end_container_path
    row.end_container_child_index = _imported_container_child_index(
        row.end_container_child_index, bm.end_container_child_index,
    )
    row.end_offset = bm.end_offset
    row.context_string = bm.context_string
    row.chapter_progress = bm.chapter_progress
    row.annotation_type = to_storage_type(bm.annotation_type)
    row.client_modified_at = device_modified_at
    row.server_modified_at = datetime.now(timezone.utc)
    row.last_synced = datetime.now(timezone.utc)
    row.last_editor_device_id = origin_device_id
    row.content_revision = (row.content_revision or 1) + 1
    # Deliberately do not assign ``hidden``. Device-side deletion is not an
    # import authority, and a recovery upload must never hide a server row.


def ingest_bookmarks(sqlite_path, user_id, session, book_lookup, commit,
                     origin_device_id=None) -> dict:
    """Walk the parsed bookmarks, resolve VolumeIDs via ``book_lookup``,
    merge annotations into ``kobo_annotation_sync``. Dependencies
    are explicit so this function is unit-testable without a Flask app.

    The annotation-backup hook fires automatically on commit so the
    user already has a recoverable snapshot.

    Existing rows are updated only when the device's valid ``DateModified`` is
    later than every accepted server/client modification clock on the row.
    This deliberately favours the server on missing, malformed, equal, or
    older device clocks: an import is a recovery operation and must not silently
    overwrite state that may have been edited after the device snapshot.

    Hidden device rows are counted but never applied. Importing device-side
    deletion is intentionally outside this recovery path's authority.

    Returns a counts dict the JSON endpoint hands back to the browser.
    """
    imported = 0
    updated = 0
    skipped_existing = 0
    skipped_orphan = 0
    skipped_hidden = 0
    skipped_empty = 0
    skipped_invalid = 0
    skipped_newer_server = 0
    skipped_invalid_content_id = 0
    failed = 0
    total_seen = 0

    # Cache: VolumeID -> CW book_id (or None for not-in-library).
    # Same VolumeID often appears across many bookmarks; resolve once.
    uuid_cache = {}

    for bm in parse_kobo_bookmarks(sqlite_path):
        total_seen += 1
        if bm.hidden:
            skipped_hidden += 1
            continue
        if not _bookmark_has_recoverable_content(
                bm.text, bm.annotation, bm.annotation_type):
            skipped_empty += 1
            continue
        if not bm.bookmark_id or not bm.volume_id:
            skipped_invalid += 1
            continue
        normalized_type = to_storage_type(bm.annotation_type)
        if normalized_type is not None and len(normalized_type) > 32:
            skipped_invalid += 1
            continue

        # Resolve VolumeID -> CW book.id. Kobo writes either a UUID or
        # ``file:///mnt/onboard/...`` for sideloaded books. We only
        # accept UUIDs; sideloaded books CW doesn't know about are
        # skipped (design doc §11 row 2).
        volume_uuid = bm.volume_id
        if volume_uuid in uuid_cache:
            book_id, resolved_uuid = uuid_cache[volume_uuid]
        else:
            book = book_lookup(volume_uuid)
            book_id = book.id if book else None
            resolved_uuid = (getattr(book, "uuid", None) or volume_uuid) if book else None
            uuid_cache[volume_uuid] = (book_id, resolved_uuid)

        if book_id is None:
            skipped_orphan += 1
            continue

        # Dedup on the CANONICAL key. `uq_annotation_user_book_annotation` is
        # (user_id, book_id, annotation_id) and the live PATCH dispatcher
        # upserts on that same triple; checking only (user_id, annotation_id)
        # made one book's row suppress a row the schema explicitly permits in
        # another book. `ix_annotation_user_book` covers this lookup.
        existing = session.query(ub.Annotation).filter(
            ub.Annotation.user_id == user_id,
            ub.Annotation.book_id == book_id,
            ub.Annotation.annotation_id == bm.bookmark_id,
        ).first()
        from .services.annotation_content_id import normalize_content_id, ContentIdError
        try:
            content_id = normalize_content_id(
                bm.content_id,
                book_uuid=resolved_uuid,
                allow_legacy_file_uri=True,
                allow_kobo_device_content_id=True,
            )
        except ContentIdError:
            skipped_invalid_content_id += 1
            continue

        device_created_at = _parse_kobo_datetime(
            bm.date_created,
            assume_naive_utc=True,
        )
        # Keep the safe default for DateModified: unlike creation metadata, this
        # clock grants overwrite authority through _device_edit_is_newer().
        device_modified_at = _parse_kobo_datetime(bm.date_modified)
        if existing is not None:
            if not _device_edit_is_newer(device_modified_at, existing):
                if _bookmark_matches_annotation(bm, content_id, existing):
                    skipped_existing += 1
                else:
                    skipped_newer_server += 1
                continue
            _apply_imported_bookmark(
                existing, bm, content_id,
                device_modified_at=device_modified_at,
                origin_device_id=origin_device_id,
            )
            updated += 1
            continue

        row = ub.Annotation(
            user_id=user_id,
            annotation_id=bm.bookmark_id,
            book_id=book_id,
            highlighted_text=bm.text,
            highlight_color=bm.color,
            note_text=bm.annotation,
            content_id=content_id,
            start_container_path=bm.start_container_path,
            start_container_child_index=bm.start_container_child_index,
            start_offset=bm.start_offset,
            end_container_path=bm.end_container_path,
            end_container_child_index=bm.end_container_child_index,
            end_offset=bm.end_offset,
            context_string=bm.context_string,
            chapter_progress=bm.chapter_progress,
            # The device's own word for what this row is, carried through
            # unchanged (F-7e418c). The live PATCH path stores the same
            # vocabulary from `payload["type"]`, so a row recovered from
            # KoboReader.sqlite and the same annotation arriving over the wire
            # now agree instead of one of them being NULL.
            annotation_type=to_storage_type(getattr(bm, "annotation_type", None)),
            source="kobo",
            origin_device_id=origin_device_id,
            hidden=False,
            # Preserve the annotation's device creation time when usable;
            # malformed/absent clocks retain the historical import-time fallback.
            created_at=device_created_at or datetime.now(timezone.utc),
            client_modified_at=device_modified_at,
            server_modified_at=datetime.now(timezone.utc),
        )
        session.add(row)
        imported += 1

    try:
        # `_commit_required` is this module's own guard for exactly this: CWNG's
        # commit wrapper signals a rolled-back write by RETURNING False, not by
        # raising, so a bare `commit()` reported rows as imported after writing
        # nothing. Routing through the helper makes both failure shapes take
        # the same path.
        _commit_required(commit)
    except Exception as e:
        log.error("annotations: import commit failed: %s", e)
        session.rollback()
        failed = imported + updated
        imported = 0
        updated = 0

    return {
        "imported": imported,
        "updated": updated,
        "skipped_existing": skipped_existing,
        "skipped_orphan": skipped_orphan,
        "skipped_hidden": skipped_hidden,
        "skipped_empty": skipped_empty,
        "skipped_invalid": skipped_invalid,
        "skipped_newer_server": skipped_newer_server,
        "skipped_invalid_content_id": skipped_invalid_content_id,
        "failed": failed,
        "total_seen": total_seen,
    }


# ---------------------------------------------------------------------------
# P4 — view + export
# ---------------------------------------------------------------------------

# Stable export column order — JSON keys + CSV columns + Markdown
# template all consume this order so the three formats stay in sync.
_EXPORT_FIELDS = (
    "annotation_id",
    "book_id",
    "highlighted_text",
    "highlight_color",
    "note_text",
    "content_id",
    "chapter_progress",
    "context_string",
    "cfi_range",
    "source",
    "created_at",
    "last_synced",
)


def _visible_user_annotations_query(session, user_id: int, book_id: int):
    """Canonical visible-set query for every per-user/per-book surface."""
    return (
        session.query(ub.Annotation)
        .filter(
            ub.Annotation.user_id == user_id,
            ub.Annotation.book_id == book_id,
        )
        .filter(
            (ub.Annotation.hidden.is_(None))
            | (ub.Annotation.hidden == False)  # noqa: E712 — SQLA needs ==
        )
    )


def count_user_annotations(user_id: int, book_id: int, session=None) -> int:
    """Count exactly the live annotation rows shown on the linked page."""
    app_session = session if session is not None else ub.session
    count = (
        _visible_user_annotations_query(app_session, user_id, book_id)
        .with_entities(func.count(ub.Annotation.id))
        .scalar()
    )
    return int(count or 0)


def _load_user_annotations(user_id: int, book_id: int) -> list:
    """Per-user-per-book read of ``kobo_annotation_sync``. Filters out
    soft-deleted rows so the view shows the live set. Stable order by
    chapter_progress so the export round-trips a sensible reading
    order even for books with hundreds of highlights."""
    return (
        _visible_user_annotations_query(ub.session, user_id, book_id)
        .order_by(
            ub.Annotation.chapter_progress.asc().nullslast(),
            ub.Annotation.created_at.asc().nullslast(),
            ub.Annotation.id.asc(),
        )
        .all()
    )


def _row_to_dict(row) -> dict:
    """Project a ``KoboAnnotationSync`` row to the export payload shape."""
    return {
        "annotation_id": row.annotation_id,
        "book_id": row.book_id,
        "highlighted_text": row.highlighted_text,
        # Stored as the canonical wire hex; exports speak the display
        # vocabulary so a Markdown/CSV/JSON dump reads as "grey", not
        # "#A0A0A0". A colour we can't name stays whatever it is, and an
        # absent one stays absent.
        "highlight_color": to_display_name(row.highlight_color),
        "note_text": row.note_text,
        "content_id": row.content_id,
        "chapter_progress": row.chapter_progress,
        "context_string": row.context_string,
        "cfi_range": row.cfi_range,
        "source": row.source,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_synced": row.last_synced.isoformat() if row.last_synced else None,
    }


def render_markdown(book_title: str, rows) -> str:
    """Render rows as a Markdown document — single H1 with the book
    title, then one block per highlight (the quoted passage, then a
    bulleted attribution line for color/note/source/chapter)."""
    out = [f"# {book_title}", ""]
    for r in rows:
        text = (r.highlighted_text or "").strip()
        # Markdown blockquote — every line of the highlight gets `> `.
        quoted = "\n".join("> " + line for line in text.splitlines() or [""])
        out.append(quoted)
        meta_bits = []
        color = to_display_name(r.highlight_color)
        if color:
            meta_bits.append(f"color: **{color}**")
        if r.note_text:
            note_oneline = r.note_text.replace("\n", " ").strip()
            meta_bits.append(f"note: {note_oneline}")
        if r.chapter_progress is not None:
            meta_bits.append(f"chapter progress: {int(r.chapter_progress * 100)}%")
        if r.source:
            meta_bits.append(f"source: {r.source}")
        if meta_bits:
            out.append("> ")
            out.append("> *" + " — ".join(meta_bits) + "*")
        out.append("")
    return "\n".join(out) + "\n"


def render_csv(rows) -> str:
    """Render rows as RFC-4180-compatible CSV. Stable column order
    matches ``_EXPORT_FIELDS`` so round-trip parsers don't have to
    detect the order from the header."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(_EXPORT_FIELDS), quoting=csv.QUOTE_MINIMAL)
    writer.writeheader()
    for r in rows:
        writer.writerow(_row_to_dict(r))
    return buf.getvalue()


def render_json(book_title: str, book_id: int, user_id: int, rows) -> str:
    """Render rows as a JSON envelope identical in shape to the
    annotation-backup snapshot format, so a power user can use either
    format interchangeably."""
    payload = {
        "schema_version": 1,
        "user_id": user_id,
        "book_id": book_id,
        "book_title": book_title,
        "annotation_count": len(rows),
        "annotations": [_row_to_dict(r) for r in rows],
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, indent=2) + "\n"


def _safe_filename_part(s: str, default: str = "book") -> str:
    """Slugify the book title for the Content-Disposition filename so
    a user-readable name doesn't trip the header parser. Strips
    everything except [A-Za-z0-9._-], collapses runs."""
    if not s:
        return default
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-")
    return cleaned or default


def _resolve_book_or_404(book_id: int):
    """Load an annotation archive book while enforcing content policy.

    A personal-library membership removal must not make the user's retained
    annotations unreadable.  Bypass only that membership predicate; language,
    tag, custom-column and hidden-book restrictions remain enforced.
    """
    book = calibre_db.get_filtered_book(
        book_id, allow_show_archived=True, allow_show_global=True,
    )
    if not book:
        abort(404)
    return book


def _resolve_epub_path(book) -> Optional[str]:
    """Find the on-disk EPUB file for a book — mirrors the lookup
    pattern in ``cps/web.py``'s serve_book. Returns ``None`` if no
    EPUB/KEPUB format exists or the file is missing on disk.

    Prefers KEPUB: Kobo highlights anchor on KoboSpan ids, which only
    exist in the kepub. A plain EPUB has no KoboSpans, so computing a
    CFI against it would never resolve the anchor."""
    from . import config

    def _disk_path(fmt):
        ext = (fmt.format or "").upper()
        if ext not in ("EPUB", "KEPUB"):
            return None
        path = os.path.join(config.get_book_path(), book.path, fmt.name + "." + ext.lower())
        return path if os.path.isfile(path) else None

    data = book.data or []
    # KEPUB first (carries KoboSpans), then any EPUB as a fallback.
    for fmt in data:
        if (fmt.format or "").upper() == "KEPUB":
            p = _disk_path(fmt)
            if p:
                return p
    for fmt in data:
        if (fmt.format or "").upper() == "EPUB":
            p = _disk_path(fmt)
            if p:
                return p
    return None


def _compute_annotation_cfi(row, book) -> Optional[str]:
    """Resolve the row's native EPUB anchor against the current book file."""
    if not row.content_id or "!!" not in (row.content_id or ""):
        return None
    epub_path = _resolve_epub_path(book)
    if not epub_path:
        return None
    from pathlib import Path as _Path
    from .services.kobo_position import compute_cfi_range, KoboPosition
    try:
        cfi = compute_cfi_range(_Path(epub_path), KoboPosition(
            content_id=row.content_id,
            start_container_path=row.start_container_path or "",
            start_container_child_index=row.start_container_child_index,
            start_offset=row.start_offset or 0,
            end_container_path=row.end_container_path or "",
            end_container_child_index=row.end_container_child_index,
            end_offset=row.end_offset or 0,
            context_string=row.context_string,
        ))
    except Exception as e:
        log.warning("annotations: cfi compute failed for %s: %s", row.annotation_id, e)
        return None
    return cfi


def _persist_cfi_range(row, cfi):
    if not cfi or row.cfi_range == cfi:
        return
    row.cfi_range = cfi
    try:
        ub.session_commit()
    except Exception as e:
        log.error("annotations: cfi persist failed: %s", e)
        ub.session.rollback()


def _ensure_cfi_range(row, book) -> Optional[str]:
    """Compute and cache a missing CFI, without revalidating cached rows."""
    if row.cfi_range:
        return row.cfi_range
    cfi = _compute_annotation_cfi(row, book)
    _persist_cfi_range(row, cfi)
    return cfi


def _resolve_annotation_anchor(row, book):
    """Return ``(cfi, status)`` against the current file.

    Native KoboSpan/child-index anchors are deliberately re-resolved even when
    a CFI was cached earlier: a replacement KEPUB may retain the database row
    while invalidating its original DOM anchor. CFI-only web-reader rows cannot
    be validated server-side without epub.js, so a structurally present CFI is
    treated as usable; the reader remains the final renderer.
    """
    position_type = getattr(row, "position_type", None)
    if position_type == "unanchored":
        # A standalone note was never placed in the book, so "unresolved" would
        # be a lie: nothing failed. Without this branch it falls through to the
        # CFI path, finds no cfi_range, and reports the same status as a
        # highlight whose anchor was destroyed by a regenerated KEPUB — the UI
        # would warn "this highlight can't be shown in the book" about a note
        # that is not a highlight and was never meant to be shown there.
        #
        # Neither this resolver nor the sentinel is wrong alone; they merged
        # without a conflict and composed into a wrong answer.
        return None, "unanchored"
    if position_type == "pdf_quad":
        return None, "ok" if row.pdf_page is not None and row.pdf_quad_json else "unresolved"
    if position_type == "comic_page":
        return None, "ok" if row.comic_page is not None else "unresolved"

    from .services.kobo_position import _extract_kobospan_id, KOBO_SELECTOR_SENTINEL
    has_selector = bool(
        _extract_kobospan_id(row.start_container_path or "")
        and _extract_kobospan_id(row.end_container_path or "")
    )
    start_child = getattr(row, "start_container_child_index", None)
    end_child = getattr(row, "end_container_child_index", None)
    has_child_anchor = (
        start_child is not None and end_child is not None
        and start_child != KOBO_SELECTOR_SENTINEL and end_child != KOBO_SELECTOR_SENTINEL
    )
    if has_selector or has_child_anchor:
        current_cfi = _compute_annotation_cfi(row, book)
        if current_cfi:
            _persist_cfi_range(row, current_cfi)
            return current_cfi, "ok"
        return row.cfi_range, "unresolved"
    return row.cfi_range, "ok" if row.cfi_range else "unresolved"


def _data_json_row(r, cfi, pdf_quad, device_public_ids=None, anchor_status=None) -> dict:
    """Project one annotation row to the web-reader's data.json shape.

    Emits the canonical KoboSpan anchor (``start_kobospan`` /
    ``end_kobospan`` + offsets + ``content_id``) — the reader regenerates
    a wrapper-aware CFI client-side from these, because a server-authored
    CFI can't account for epub.js's render-time wrapper divs. ``cfi_range``
    is the portable source CFI, kept for the sidebar "jump" fallback and
    export parity. Pure + dependency-free so the payload contract is
    unit-testable without a Flask request context."""
    from .services.kobo_position import _extract_kobospan_id
    device_public_ids = device_public_ids or {}
    if anchor_status is None:
        anchor_status = "ok" if (
            cfi or pdf_quad or getattr(r, "comic_page", None) is not None
        ) else "unresolved"
    return {
        "annotation_id": r.annotation_id,
        "cfi_range": cfi,
        "content_id": r.content_id,
        "start_kobospan": _extract_kobospan_id(r.start_container_path or ""),
        "start_offset": r.start_offset,
        "end_kobospan": _extract_kobospan_id(r.end_container_path or ""),
        "end_offset": r.end_offset,
        "highlighted_text": r.highlighted_text,
        # The display token, or null. It used to say "yellow" whenever the
        # column was NULL, which handed the reader a real-looking colour for a
        # row that has none (a standalone note) and for one whose colour we
        # failed to resolve. The client palettes already carry their own
        # fallback for null, so the server no longer asserts a colour it does
        # not have.
        "highlight_color": to_display_name(r.highlight_color),
        "note_text": r.note_text,
        "chapter_progress": r.chapter_progress,
        "source": r.source,
        "origin_device_id": device_public_ids.get(getattr(r, "origin_device_id", None)),
        "assigned_device_id": device_public_ids.get(getattr(r, "assigned_device_id", None)),
        "anchor_status": anchor_status,
        "position_type": getattr(r, "position_type", None),
        "pdf_page": getattr(r, "pdf_page", None),
        "pdf_quad": pdf_quad,
        "comic_page": getattr(r, "comic_page", None),
    }


def _annotation_device_payload(user_id, session, device_ids=None):
    """Return a bounded internal→public lookup for referenced devices only."""
    if device_ids is not None and not device_ids:
        return {}, {}
    query = session.query(ub.Device).filter(ub.Device.user_id == user_id)
    if device_ids is not None:
        query = query.filter(ub.Device.id.in_(tuple(device_ids)))
    devices = query.order_by(ub.Device.id).limit(MAX_DEVICE_LIST_LIMIT).all()
    public_ids = {device.id: device.public_id for device in devices}
    payload = {
        device.public_id: {
            "label": device.display_name,
            "model": device.model,
            "type": device.kind,
        }
        for device in devices
    }
    return public_ids, payload


@annotations_bp.route("/annotations/<int:book_id>/data.json", methods=["GET"])
@user_login_required
def annotations_data(book_id):
    """Lightweight JSON list for the web reader — every visible
    annotation for the current user + book, with cfi_range computed on
    the fly + cached if missing. Excludes hidden rows.

    Sub-projects (3)/(4): also emits ``position_type``, ``pdf_page``,
    ``pdf_quad`` (parsed from ``pdf_quad_json``) and ``comic_page`` so the
    PDF / comic reader JS can decide how to overlay each row.
    """
    book = _resolve_book_or_404(book_id)
    rows = _load_user_annotations(current_user.id, book_id)
    referenced_device_ids = {
        device_id
        for row in rows
        for device_id in (row.origin_device_id, row.assigned_device_id)
        if device_id is not None
    }
    device_public_ids, devices = _annotation_device_payload(
        current_user.id, ub.session, device_ids=referenced_device_ids,
    )
    out = []
    for r in rows:
        # CFI computation only applies to EPUB-origin rows. For PDF/comic
        # rows, skip the lookup — they have their own position fields.
        cfi, anchor_status = _resolve_annotation_anchor(r, book)
        pdf_quad = None
        if r.pdf_quad_json:
            try:
                pdf_quad = json.loads(r.pdf_quad_json)
            except (ValueError, TypeError):
                pdf_quad = None
        out.append(_data_json_row(
            r, cfi, pdf_quad, device_public_ids, anchor_status=anchor_status,
        ))
    return jsonify({"annotations": out, "annotation_count": len(out), "devices": devices})


@annotations_bp.route("/annotations/<int:book_id>", methods=["GET"])
@user_login_required
def annotations_view(book_id):
    """Per-book view — every annotation the current user has for this
    book, grouped by chapter, sorted by chapter_progress."""
    book = _resolve_book_or_404(book_id)
    rows = _load_user_annotations(current_user.id, book_id)
    return render_title_template(
        "annotations_view.html",
        title=_(u"Annotations: %(title)s", title=book.title),
        page="annotations_view",
        book=book,
        annotations=rows,
        export_md_url=url_for("annotations.annotations_export_markdown", book_id=book_id),
        export_csv_url=url_for("annotations.annotations_export_csv", book_id=book_id),
        export_json_url=url_for("annotations.annotations_export_json", book_id=book_id),
    )


@annotations_bp.route("/annotations/<int:book_id>/export.md", methods=["GET"])
@user_login_required
def annotations_export_markdown(book_id):
    book = _resolve_book_or_404(book_id)
    rows = _load_user_annotations(current_user.id, book_id)
    body = render_markdown(book.title, rows)
    fname = f"{_safe_filename_part(book.title)}-highlights.md"
    return Response(
        body,
        mimetype="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@annotations_bp.route("/annotations/<int:book_id>/export.csv", methods=["GET"])
@user_login_required
def annotations_export_csv(book_id):
    book = _resolve_book_or_404(book_id)
    rows = _load_user_annotations(current_user.id, book_id)
    body = render_csv(rows)
    fname = f"{_safe_filename_part(book.title)}-highlights.csv"
    return Response(
        body,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@annotations_bp.route("/annotations/<int:book_id>/export.json", methods=["GET"])
@user_login_required
def annotations_export_json(book_id):
    book = _resolve_book_or_404(book_id)
    rows = _load_user_annotations(current_user.id, book_id)
    body = render_json(book.title, book_id, current_user.id, rows)
    fname = f"{_safe_filename_part(book.title)}-highlights.json"
    return Response(
        body,
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ---------------------------------------------------------------------------
# P5 — web-reader create / edit / delete
# ---------------------------------------------------------------------------

# Web-created highlights get an origin-tagged id so logs/exports can tell them
# apart from a Kobo device's BookmarkID UUID, and so Phase 2's device bridge
# can recognize a row it should materialize on a device.
WEBREADER_ID_PREFIX = "cwn-web-"

# The colors the web reader's palette offers. Defined once in
# ``services/annotation_colors`` and re-exported here for the callers that have
# always imported it from this module.
#
# NOT "the set a Kobo round-trips" — that claim was wrong. A Kobo round-trips
# yellow/pink/blue/green/grey and has no red at all (finding F-5769c9); red is
# a CWNG web-reader colour with its own canonical hex. Widening what the reader
# offers is a product decision, so this stays the four it has always accepted.
WEBREADER_COLORS = WEBREADER_COLOR_NAMES


def create_annotation(payload, *, user_id, book, session, commit,
                      origin_device_id=None):
    """Create a ``source='webreader'`` annotation from a reader selection.

    ``payload`` carries the KoboSpan anchors the reader derived from the live
    kepub DOM: ``start_kobospan`` / ``end_kobospan`` (bare ids like
    ``kobo.4.1``), ``start_offset`` / ``end_offset``, ``content_id``,
    ``highlighted_text``, ``highlight_color``, optional ``note_text``.

    Stores the selection as Kobo-native fields (``span#<id>`` selector + the
    ``-99`` kepub child-index sentinel) so the same converter that renders
    device highlights renders these, and so Phase 2 can push them to a device.
    Computes a portable ``cfi_range`` when the kepub is on disk; a missing CFI
    is fine (the reader rebuilds one client-side from the KoboSpan id).

    Pure-ish: dependencies are explicit so it's unit-testable without a Flask
    request context — mirrors :func:`ingest_bookmarks`. Raises ``ValueError``
    on a payload with no usable anchor.
    """
    # The reader sends a palette NAME; the column speaks canonical hex. Accept
    # the name (unchanged UI contract), store the hex.
    color_name = (payload.get("highlight_color") or "yellow").strip().lower()
    if color_name not in WEBREADER_COLORS:
        color_name = "yellow"
    color = to_storage_color(color_name)

    # content_id is "<book_uuid>!!<chapter_file>". The reader knows the chapter
    # href but not the book uuid, so when it sends a bare chapter_filename we
    # build the Kobo-valid form here (the server has the Book). A directly
    # supplied content_id wins.
    content_id = payload.get("content_id")
    if not content_id:
        chapter = (payload.get("chapter_filename") or "").strip()
        book_uuid = getattr(book, "uuid", None)
        if chapter and book_uuid:
            content_id = f"{book_uuid}!!{chapter}"
    if content_id is not None:
        from .services.annotation_content_id import normalize_content_id
        content_id = normalize_content_id(content_id, book_uuid=getattr(book, "uuid", None))

    start_span = (payload.get("start_kobospan") or "").strip()
    cfi_range = (payload.get("cfi_range") or "").strip()

    # A standalone note (#325): a thought about the book that is not attached to
    # any passage. Deliberately explicit rather than inferred from "no anchor
    # supplied", because a highlight that lost its anchor is a bug and must keep
    # raising below.
    #
    # It carries NO position at all — not even the "cfi"/-99 pair the CFI-only
    # branch uses for Kobo compatibility. That pair is a sentinel meaning "web
    # origin, no KoboSpan"; putting it on a row with nothing to point at would
    # make the row look like a pushable highlight to any code that keys on the
    # container fields, and the device would end up with a note at a position we
    # invented. A Kobo cannot represent this row at all, and that is fine — it is
    # a CWNG-native concept, marked so it can be excluded by predicate rather
    # than guessed at.
    #
    # NOTE FOR ANYONE ADDING A FIELD TO WEB-READER ROWS: this is the THIRD
    # ub.Annotation(...) constructor in this function, alongside the CFI-only
    # branch below and the KoboSpan one after it. A field added to the other two
    # and missed here produces a row that is valid, merges without conflict, and
    # is silently missing that field for every standalone note. Add it in all
    # three or none.
    #
    if (payload.get("position_type") or "").strip() == "unanchored":
        note = (payload.get("note_text") or "").strip()
        if not note:
            raise ValueError("create_annotation: an unanchored note needs note_text")
        progress = payload.get("chapter_progress")
        row = ub.Annotation(
            user_id=user_id,
            annotation_id=WEBREADER_ID_PREFIX + uuid.uuid4().hex,
            book_id=book.id,
            source="webreader",
            # No anchor: the reader's unanchored note, an object no Kobo can
            # represent. `note` is declared web-reader-only in annotation_types
            # for the same reason WEBREADER_RED_HEX is declared there.
            annotation_type=type_for_webreader_annotation(has_anchor=False),
            note_text=note,
            # A standalone note is made on a device like any other annotation;
            # it just cannot be placed in the book. Attribution is orthogonal to
            # anchoring, so it carries an origin exactly like the other two.
            origin_device_id=origin_device_id,
            last_editor_device_id=origin_device_id,
            # No highlighted passage, so no colour to render on it.
            highlighted_text=None,
            highlight_color=None,
            content_id=content_id,
            position_type="unanchored",
            # Ordering only ("roughly here in the book"), never an anchor: a
            # future push path must not be able to take it for a position.
            chapter_progress=float(progress) if progress is not None else None,
            context_string=payload.get("context_string"),
            hidden=False,
        )
        session.add(row)
        _commit_required(commit)
        return row

    # The SPA epub.js reader produces a portable EPUB CFI (not a KoboSpan). Accept
    # a CFI-only web-reader highlight: it stands on its cfi_range, exports like any
    # other, and renders back in the reader. KoboSpan stays the path for the kepub
    # reader / device sync (no kobospan => nothing to push to a Kobo, which is
    # correct for a web-origin highlight).
    if not start_span:
        if not cfi_range:
            raise ValueError("create_annotation: missing start_kobospan or cfi_range anchor")
        row = ub.Annotation(
            user_id=user_id,
            annotation_id=WEBREADER_ID_PREFIX + uuid.uuid4().hex,
            book_id=book.id,
            source="webreader",
            # An anchored passage is a `highlight` — the DEVICE's own word for
            # the same object, so this is not a vocabulary we invented. Note
            # text attached to it does not make it a note.
            annotation_type=type_for_webreader_annotation(has_anchor=True),
            origin_device_id=origin_device_id,
            last_editor_device_id=origin_device_id,
            highlighted_text=payload.get("highlighted_text"),
            highlight_color=color,
            note_text=payload.get("note_text"),
            content_id=content_id,
            cfi_range=cfi_range,
            position_type="cfi",
            start_container_path="cfi",
            start_container_child_index=-99,
            start_offset=0,
            end_container_path="cfi",
            end_container_child_index=-99,
            end_offset=0,
            context_string=payload.get("context_string"),
            hidden=False,
        )
        session.add(row)
        _commit_required(commit)
        return row

    end_span = (payload.get("end_kobospan") or "").strip() or start_span

    row = ub.Annotation(
        user_id=user_id,
        annotation_id=WEBREADER_ID_PREFIX + uuid.uuid4().hex,
        book_id=book.id,
        source="webreader",
        # An anchored passage is a `highlight` — the DEVICE's own word for
        # the same object, so this is not a vocabulary we invented. Note
        # text attached to it does not make it a note.
        annotation_type=type_for_webreader_annotation(has_anchor=True),
        origin_device_id=origin_device_id,
        last_editor_device_id=origin_device_id,
        highlighted_text=payload.get("highlighted_text"),
        highlight_color=color,
        note_text=payload.get("note_text"),
        content_id=content_id,
        start_container_path="span#" + start_span,
        start_container_child_index=-99,
        start_offset=int(payload.get("start_offset") or 0),
        end_container_path="span#" + end_span,
        end_container_child_index=-99,
        end_offset=int(payload.get("end_offset") or 0),
        context_string=payload.get("context_string"),
        hidden=False,
    )
    session.add(row)
    # Compute the portable CFI for export. Never fatal — the row stands on its
    # KoboSpan anchor regardless. _ensure_cfi_range persists it itself.
    try:
        _ensure_cfi_range(row, book)
    except Exception as e:  # pragma: no cover - defensive
        log.warning("annotations: cfi compute on create failed: %s", e)
    _commit_required(commit)
    return row


# Sentinel for "field not supplied" so edit can distinguish "set note to None"
# (clear it) from "don't touch the note".
_UNSET = object()


class AssignmentError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _find_owned_annotation(annotation_id, user_id, book_id, session):
    """Resolve a single annotation scoped to its owner — the IDOR guard. A row
    that belongs to another user (or doesn't exist) is invisible: returns
    ``None`` so callers 404 rather than leaking/mutating a foreign row."""
    return (
        session.query(ub.Annotation)
        .filter(
            ub.Annotation.user_id == user_id,
            ub.Annotation.book_id == book_id,
            ub.Annotation.annotation_id == annotation_id,
        )
        .first()
    )


def edit_annotation(annotation_id, *, user_id, book_id, session, commit,
                    color=_UNSET, note=_UNSET, editor_device_id=None):
    """Update an annotation's color and/or note. Position is immutable.

    Returns the row, or ``None`` if no annotation with that id belongs to
    ``(user_id, book_id)``. Raises ``ValueError`` on an unsupported color.
    """
    row = _find_owned_annotation(annotation_id, user_id, book_id, session)
    if row is None:
        return None
    if color is not _UNSET:
        normalized = (color or "").strip().lower()
        if normalized not in WEBREADER_COLORS:
            raise ValueError(f"edit_annotation: unsupported color {color!r}")
        # Validate the name the reader sent, store the canonical hex.
        row.highlight_color = to_storage_color(normalized)
    if note is not _UNSET:
        row.note_text = note
    if editor_device_id is not None:
        row.last_editor_device_id = editor_device_id
    now = datetime.now(timezone.utc)
    row.content_revision = (row.content_revision or 1) + 1
    row.server_modified_at = now
    row.last_synced = now
    if commit is not None:
        _commit_required(commit)
    return row


def reassign_annotation(annotation_id, *, user_id, book_id, assigned_device_public_id,
                        expected_routing_revision, session, commit):
    """Change routing intent while retaining provenance and old device state."""
    row = _find_owned_annotation(annotation_id, user_id, book_id, session)
    if row is None:
        raise AssignmentError("not_found")
    if expected_routing_revision is not None:
        if isinstance(expected_routing_revision, bool) or not isinstance(expected_routing_revision, int):
            raise AssignmentError("invalid_revision")
        if (row.routing_revision or 1) != expected_routing_revision:
            raise AssignmentError("revision_conflict")
    target = None
    if assigned_device_public_id is not None:
        if not isinstance(assigned_device_public_id, str):
            raise AssignmentError("device_not_found")
        target = _owned_device(assigned_device_public_id, user_id, session)
        if target is None:
            raise AssignmentError("device_not_found")
        if not target.active:
            raise AssignmentError("device_inactive")
    target_id = target.id if target else None
    old_id = row.assigned_device_id
    if old_id == target_id:
        if target is not None:
            state = session.query(ub.AnnotationDeviceState).filter_by(
                annotation_id=row.id, device_id=target.id,
            ).first()
            if state is None:
                session.add(ub.AnnotationDeviceState(
                    annotation_id=row.id, device_id=target.id,
                    desired=True, delivery_status="pending",
                ))
            else:
                state.desired = True
        if commit is not None:
            try:
                _commit_required(commit)
            except RuntimeError as error:
                raise AssignmentError("database_error") from error
        else:
            session.flush()
        return row
    if old_id is not None:
        old_state = session.query(ub.AnnotationDeviceState).filter_by(
            annotation_id=row.id, device_id=old_id,
        ).first()
        if old_state is None:
            old_state = ub.AnnotationDeviceState(
                annotation_id=row.id, device_id=old_id, desired=False, delivery_status="pending",
            )
            session.add(old_state)
        else:
            old_state.desired = False
    if target is not None:
        new_state = session.query(ub.AnnotationDeviceState).filter_by(
            annotation_id=row.id, device_id=target.id,
        ).first()
        if new_state is None:
            new_state = ub.AnnotationDeviceState(
                annotation_id=row.id, device_id=target.id, desired=True, delivery_status="pending",
            )
            session.add(new_state)
        else:
            new_state.desired = True
            new_state.delivery_status = "pending"
            new_state.last_error_code = None
    row.assigned_device_id = target_id
    row.routing_revision = (row.routing_revision or 1) + 1
    if commit is not None:
        try:
            _commit_required(commit)
        except RuntimeError as error:
            raise AssignmentError("database_error") from error
    else:
        session.flush()
    return row


def bulk_reassign_annotations(items, *, user_id, assigned_device_public_id, session, commit):
    """Apply and commit every item independently, returning mixed results."""
    results = []
    for item in items:
        annotation_id = item.get("annotation_id") if isinstance(item, dict) else None
        try:
            if not isinstance(item, dict) or not isinstance(item.get("book_id"), int) or not annotation_id:
                raise AssignmentError("invalid_item")
            reassign_annotation(
                annotation_id, user_id=user_id, book_id=item["book_id"],
                assigned_device_public_id=assigned_device_public_id,
                expected_routing_revision=item.get("expected_routing_revision"),
                session=session, commit=commit,
            )
            results.append({"annotation_id": annotation_id, "ok": True})
        except AssignmentError as error:
            session.rollback()
            results.append({"annotation_id": annotation_id, "ok": False, "error_code": error.code})
        except Exception:
            session.rollback()
            log.exception("annotations: per-item reassignment failed")
            results.append({"annotation_id": annotation_id, "ok": False, "error_code": "database_error"})
    return results


def delete_annotation(annotation_id, *, user_id, book_id, session, commit,
                      editor_device_id=None):
    """Soft-delete an annotation (``hidden=True``). Idempotent: deleting an
    already-hidden row resolves + returns it (route 200). Returns ``None`` when
    no such row belongs to ``(user_id, book_id)`` (route 404)."""
    row = _find_owned_annotation(annotation_id, user_id, book_id, session)
    if row is None:
        return None
    was_hidden = bool(row.hidden)
    row.hidden = True
    if editor_device_id is not None:
        row.last_editor_device_id = editor_device_id
    now = datetime.now(timezone.utc)
    if not was_hidden:
        row.content_revision = (row.content_revision or 1) + 1
        row.server_modified_at = now
    row.last_synced = now
    _commit_required(commit)
    return row


def _fanout_to_sync_targets(row, book):
    """Push a freshly created/edited web-reader row to enabled sync targets
    (Hardcover today). Never fatal — a remote being down must not fail the
    local highlight."""
    try:
        from .services import annotation_sync
        annotation_sync.dispatch_existing_annotation_sync(row, book, current_user)
    except Exception as e:  # pragma: no cover - defensive
        log.warning("annotations: sync-target fan-out failed: %s", e)


def _observe_webreader_request_device():
    """Resolve this browser without ever exposing its installation id."""
    try:
        from .services.device_registry import (
            WEBREADER_INSTALLATION_ID_HEADER,
            ensure_webreader_device_best_effort,
        )
        device_id = ensure_webreader_device_best_effort(
            user_id=current_user.id,
            installation_id=request.headers.get(WEBREADER_INSTALLATION_ID_HEADER),
        )
    except Exception:
        log.warning("annotations: web-reader attribution failed", exc_info=True)
        device_id = None
    g.annotation_origin_device_id = device_id
    return device_id


@annotations_bp.route("/annotations/<int:book_id>", methods=["POST"])
@user_login_required
def annotations_create(book_id):
    """Create a highlight from a web-reader selection (source='webreader')."""
    book = _resolve_book_or_404(book_id)
    payload = request.get_json(silent=True) or {}
    origin_device_id = _observe_webreader_request_device()
    try:
        row = create_annotation(
            payload, user_id=current_user.id, book=book,
            session=ub.session, commit=ub.session_commit,
            origin_device_id=origin_device_id,
        )
    except ValueError as e:
        return jsonify({"error": "bad_anchor", "message": str(e)}), 400
    except (RuntimeError, SQLAlchemyError):
        return _database_error_response("single annotation create")
    _fanout_to_sync_targets(row, book)
    # Resolve the device map, or the row we just attributed answers
    # origin_device_id: null and the reader renders "Unknown device" for the
    # one highlight the user just watched itself be created.
    device_public_ids, _ = _annotation_device_payload(
        current_user.id,
        ub.session,
        device_ids={
            device_id
            for device_id in (row.origin_device_id, row.assigned_device_id)
            if device_id is not None
        },
    )
    return jsonify(_data_json_row(row, row.cfi_range, None, device_public_ids)), 201


@annotations_bp.route("/annotations/<int:book_id>/<annotation_id>", methods=["PATCH"])
@user_login_required
def annotations_edit(book_id, annotation_id):
    """Edit a highlight's color and/or note (position immutable)."""
    book = _resolve_book_or_404(book_id)
    data = request.get_json(silent=True) or {}
    editor_device_id = _observe_webreader_request_device()
    kwargs = {}
    if "highlight_color" in data:
        kwargs["color"] = data.get("highlight_color")
    if "note_text" in data:
        kwargs["note"] = data.get("note_text")
    try:
        if "assigned_device_id" in data:
            reassign_annotation(
                annotation_id, user_id=current_user.id, book_id=book_id,
                assigned_device_public_id=data.get("assigned_device_id"),
                expected_routing_revision=data.get("expected_routing_revision"),
                session=ub.session, commit=None,
            )
        row = edit_annotation(
            annotation_id, user_id=current_user.id, book_id=book_id,
            session=ub.session, commit=ub.session_commit,
            editor_device_id=editor_device_id, **kwargs,
        )
    except AssignmentError as error:
        ub.session.rollback()
        status = 404 if error.code in ("not_found", "device_not_found") else (
            409 if error.code in ("revision_conflict", "device_inactive") else (
                500 if error.code == "database_error" else 400
            )
        )
        return jsonify({"error": error.code}), status
    except ValueError as e:
        ub.session.rollback()
        return jsonify({"error": "bad_color", "message": str(e)}), 400
    except (RuntimeError, SQLAlchemyError):
        return _database_error_response("single annotation update")
    if row is None:
        abort(404)
    _fanout_to_sync_targets(row, book)
    # Same map data.json uses, so BOTH device fields resolve here. Without it
    # only assigned_device_id was patched back in below and origin_device_id
    # answered null on a row that has one.
    device_public_ids, _ = _annotation_device_payload(
        current_user.id,
        ub.session,
        device_ids={
            device_id
            for device_id in (row.origin_device_id, row.assigned_device_id)
            if device_id is not None
        },
    )
    response = _data_json_row(row, row.cfi_range, None, device_public_ids)
    response.update({"routing_revision": row.routing_revision})
    return jsonify(response), 200


@annotations_bp.route("/api/annotations/assignments/bulk", methods=["POST"])
@user_login_required
def annotation_assignments_bulk():
    data = request.get_json(silent=True) or {}
    items = data.get("items")
    if "assigned_device_id" not in data:
        return jsonify({"error": "missing_assigned_device_id"}), 400
    if not isinstance(items, list) or not items or len(items) > 500:
        return jsonify({"error": "invalid_items", "max_items": 500}), 400
    results = bulk_reassign_annotations(
        items, user_id=current_user.id,
        assigned_device_public_id=data.get("assigned_device_id"),
        session=ub.session, commit=ub.session_commit,
    )
    return jsonify({"results": results}), 200


@annotations_bp.route("/annotations/<int:book_id>/<annotation_id>", methods=["DELETE"])
@user_login_required
def annotations_delete(book_id, annotation_id):
    """Soft-delete a highlight + tombstone any remote sync targets."""
    _resolve_book_or_404(book_id)
    editor_device_id = _observe_webreader_request_device()
    try:
        row = delete_annotation(
            annotation_id, user_id=current_user.id, book_id=book_id,
            session=ub.session, commit=ub.session_commit,
            editor_device_id=editor_device_id,
        )
    except (RuntimeError, SQLAlchemyError):
        return _database_error_response("single annotation delete")
    if row is None:
        abort(404)
    # Propagate the delete to any remote sync targets (Hardcover) — no-op when
    # the row has none. Idempotent (re-sets hidden=True, skips tombstones).
    try:
        from .services import annotation_sync
        annotation_sync.dispatch_annotation_deletes(
            [annotation_id], current_user, book_id=book_id,
            # This is an authenticated user's explicit delete, so it is
            # authoritative across provenance rather than device-scoped.
            deletable_sources=None,
        )
    except Exception as e:  # pragma: no cover - defensive
        log.warning("annotations: delete fan-out failed: %s", e)
    return jsonify({"status": "deleted", "annotation_id": annotation_id}), 200
