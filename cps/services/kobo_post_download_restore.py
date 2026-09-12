# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Make a Kobo re-download harmless: remember it, then restore from CWNG rows.

Nickel treats every (re-)download as "forget what I had for this book": it
empties the book's local annotations and asks ``/annotations`` for the
replacement set, and it re-reports the reading position from the start.
The entitlement ledger (#1925) stops *spurious* re-sends; this module covers
the re-sends that are legitimate (a re-converted file) or unavoidable, so the
reader never loses her highlights again.

Two halves:

* :func:`record_download` runs on the Kobo file-download route and upserts one
  ``pending`` row per (device, book).
* :func:`consume_pending_download` is asked by the owned annotation GET; when
  a pending row exists for the requesting device it is consumed and the
  caller serves CWNG's own rows instead of proxying.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import SQLAlchemyError

from cps import ub

RESTORE_PENDING = "pending"
RESTORE_SERVED = "served"
RESTORE_EMPTY = "empty"


def _now():
    return datetime.now(timezone.utc)


def record_download(*, device_id, book_id, book_format, log):
    """Upsert the pending-restore row; best effort, never raises to the route."""
    if device_id is None or book_id is None:
        return None
    try:
        row = (
            ub.session.query(ub.KoboDeviceBookDownload)
            .filter(
                ub.KoboDeviceBookDownload.device_id == device_id,
                ub.KoboDeviceBookDownload.book_id == book_id,
            )
            .one_or_none()
        )
        if row is None:
            row = ub.KoboDeviceBookDownload(device_id=device_id, book_id=book_id)
            ub.session.add(row)
        row.book_format = (book_format or "")[:16] or None
        row.downloaded_at = _now()
        row.restore_state = RESTORE_PENDING
        row.restored_at = None
        row.restored_count = None
        ub.session.commit()
        return row
    except SQLAlchemyError:
        ub.session.rollback()
        log.warning(
            "Could not record Kobo download device_id=%s book_id=%s",
            device_id, book_id, exc_info=True,
        )
        return None


def pending_download(*, device_id, book_id):
    """Return the pending row for this device/book, or ``None``."""
    if device_id is None or book_id is None:
        return None
    return (
        ub.session.query(ub.KoboDeviceBookDownload)
        .filter(
            ub.KoboDeviceBookDownload.device_id == device_id,
            ub.KoboDeviceBookDownload.book_id == book_id,
            ub.KoboDeviceBookDownload.restore_state == RESTORE_PENDING,
        )
        .one_or_none()
    )


def settle_pending_download(row, *, state, count=None):
    """Mark the pending row consumed. The caller owns the commit."""
    row.restore_state = state
    row.restored_at = _now()
    row.restored_count = count
