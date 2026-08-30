# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.
"""Per-device reading-position journal and rehydrate latch (#1942 M3).

The user-level ``KoboReadingState`` graph remains the resolved carrier served
to Kobo, Hardcover, KOSync, and the book-detail UI. This module stores each
device's own observation without committing; request handlers retain ownership
of their existing transaction boundaries.
"""

from datetime import datetime, timezone

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .. import ub


def _now():
    return datetime.now(timezone.utc)


def rehydrate_request_cutoff():
    """Return the server clock fence for one sync request.

    A latch whose ``server_modified_at`` is at or beyond this fence was armed
    during the current request.  It cannot be consumed until a later request,
    after the device has had a chance to apply the entitlement/download that
    may reset its local position.
    """
    return _now()


def timestamp_is_newer(candidate, current):
    """Compare SQLite clocks while tolerating naive/aware round trips."""
    if candidate is None:
        return False
    if current is None:
        return True
    if (candidate.tzinfo is None) != (current.tzinfo is None):
        candidate = candidate.replace(tzinfo=None)
        current = current.replace(tzinfo=None)
    return candidate > current


def stage_position(
    *,
    device_id,
    book_id,
    progress_percent=None,
    content_source_progress_percent=None,
    location_source=None,
    location_type=None,
    location_value=None,
    cfi=None,
    client_modified_at=None,
    session=None,
):
    """Upsert one device observation and return its prior rehydrate latch.

    An ordinary position write never clears ``rehydrate_needed``. That latch
    belongs to the sync response which actually emits the repair and is
    cleared only in that request's checked commit.
    """
    if not device_id:
        return False
    s = session if session is not None else ub.session
    device_id = int(device_id)
    book_id = int(book_id)
    with s.no_autoflush:
        existing = s.query(ub.DeviceReadingPosition).filter(
            ub.DeviceReadingPosition.device_id == device_id,
            ub.DeviceReadingPosition.book_id == book_id,
        ).one_or_none()
        rehydrate_pending = bool(
            existing is not None and existing.rehydrate_needed,
        )

    now = _now()
    values = {
        "device_id": device_id,
        "book_id": book_id,
        "location_source": location_source,
        "location_type": location_type,
        "location_value": location_value,
        "progress_percent": progress_percent,
        "content_source_progress_percent": content_source_progress_percent,
        "cfi": cfi,
        "client_modified_at": client_modified_at,
        "server_modified_at": now,
        "rehydrate_needed": rehydrate_pending,
    }
    statement = sqlite_insert(ub.DeviceReadingPosition).values(**values)
    updates = {
        "location_source": statement.excluded.location_source,
        "location_type": statement.excluded.location_type,
        "location_value": statement.excluded.location_value,
        "progress_percent": statement.excluded.progress_percent,
        "content_source_progress_percent": (
            statement.excluded.content_source_progress_percent
        ),
        "cfi": statement.excluded.cfi,
        "server_modified_at": statement.excluded.server_modified_at,
    }
    # A rejected/missing device clock is not a newer observation and must not
    # erase the last usable ordering clock from this device's journal.
    if client_modified_at is not None:
        updates["client_modified_at"] = statement.excluded.client_modified_at
    s.execute(statement.on_conflict_do_update(
        index_elements=["device_id", "book_id"],
        set_=updates,
    ))
    return rehydrate_pending


def mark_rehydrate_needed(device_id, book_ids, *, session=None):
    """Arm selected books for one device, creating blank journal rows."""
    if not device_id:
        return 0
    normalized = sorted({int(book_id) for book_id in book_ids})
    if not normalized:
        return 0
    s = session if session is not None else ub.session
    now = _now()
    rows = [
        {
            "device_id": int(device_id),
            "book_id": book_id,
            "server_modified_at": now,
            "rehydrate_needed": True,
        }
        for book_id in normalized
    ]
    statement = sqlite_insert(ub.DeviceReadingPosition).values(rows)
    s.execute(statement.on_conflict_do_update(
        index_elements=["device_id", "book_id"],
        set_={
            "server_modified_at": statement.excluded.server_modified_at,
            "rehydrate_needed": True,
        },
    ))
    return len(normalized)


def mark_existing_positions_for_rehydrate(device_id, *, session=None):
    """Arm every existing journal row during the legacy synced-book reset."""
    if not device_id:
        return 0
    s = session if session is not None else ub.session
    return s.query(ub.DeviceReadingPosition).filter(
        ub.DeviceReadingPosition.device_id == int(device_id),
    ).update(
        {
            ub.DeviceReadingPosition.rehydrate_needed: True,
            ub.DeviceReadingPosition.server_modified_at: _now(),
        },
        synchronize_session=False,
    )
