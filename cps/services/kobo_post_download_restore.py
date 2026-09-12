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

# ``armed``: the sync response told this device the book Changed (Nickel will
# de-download it); the file download has not been seen yet. ``pending``: the
# device downloaded the file. Both mean "the device's local set is empty or
# about to be", so an annotation GET is answered from CWNG rows. An armed row
# stays armed after a GET (the download, and its own GET, are still to come);
# a pending row is settled by the first GET.
RESTORE_ARMED = "armed"
RESTORE_PENDING = "pending"
RESTORE_SERVED = "served"
RESTORE_EMPTY = "empty"
RESTORE_OPEN_STATES = (RESTORE_ARMED, RESTORE_PENDING)


def _now():
    return datetime.now(timezone.utc)


def resolve_download_device(*, user_id, book_id, log):
    """Best-effort device for a headerless Kobo file download.

    OBSERVED on the household instance: Nickel fetches the file with the auth
    token in the URL and none of the ``x-kobo-*`` headers the store API
    carries, so the request resolves to no device. Prefer a device of this
    user whose row for the book is already armed (the sync that announced
    the change identified it); otherwise the user's most recently seen active
    Kobo, which is the one that just synced.
    """
    if user_id is None:
        return None
    try:
        user_devices = ub.session.query(ub.Device).filter(
            ub.Device.user_id == int(user_id),
            ub.Device.kind == "kobo",
            ub.Device.active.is_(True),
        ).order_by(ub.Device.last_seen_at.desc(), ub.Device.id.desc()).all()
        if not user_devices:
            return None
        if book_id is not None:
            armed = (
                ub.session.query(ub.KoboDeviceBookDownload)
                .filter(
                    ub.KoboDeviceBookDownload.book_id == int(book_id),
                    ub.KoboDeviceBookDownload.restore_state == RESTORE_ARMED,
                    ub.KoboDeviceBookDownload.device_id.in_(
                        [device.id for device in user_devices],
                    ),
                )
                .order_by(ub.KoboDeviceBookDownload.downloaded_at.desc())
                .first()
            )
            if armed is not None:
                return armed.device_id
        return user_devices[0].id
    except SQLAlchemyError:
        ub.session.rollback()
        log.warning(
            "Could not resolve the downloading Kobo for user_id=%s book_id=%s",
            user_id, book_id, exc_info=True,
        )
        return None


def arm_pending_restore(*, device_id, book_ids, log):
    """Stage ``armed`` rows for books a sync just re-sent to a device that
    already held them. No commit: the sync's checked commit owns it, so the
    arming lands exactly with the page that told the device to re-download.
    """
    if device_id is None or not book_ids:
        return 0
    armed = 0
    try:
        existing = {
            row.book_id: row
            for row in ub.session.query(ub.KoboDeviceBookDownload).filter(
                ub.KoboDeviceBookDownload.device_id == int(device_id),
                ub.KoboDeviceBookDownload.book_id.in_(list(book_ids)),
            ).all()
        }
        for book_id in book_ids:
            row = existing.get(book_id)
            if row is None:
                row = ub.KoboDeviceBookDownload(
                    device_id=int(device_id), book_id=int(book_id),
                )
                ub.session.add(row)
            elif row.restore_state == RESTORE_PENDING:
                # A download already happened; keep the stronger state.
                continue
            row.downloaded_at = _now()
            row.restore_state = RESTORE_ARMED
            row.restored_at = None
            row.restored_count = None
            armed += 1
        ub.session.flush()
    except SQLAlchemyError:
        log.warning(
            "Could not arm Kobo post-download restore device_id=%s books=%s",
            device_id, sorted(book_ids), exc_info=True,
        )
        raise
    return armed


def record_download(*, device_id, book_id, book_format, log, user_id=None):
    """Upsert the pending-restore row; best effort, never raises to the route."""
    if book_id is None:
        return None
    if device_id is None:
        device_id = resolve_download_device(
            user_id=user_id, book_id=book_id, log=log,
        )
        if device_id is None:
            log.info(
                "Kobo download of book %s: no device resolved for user_id=%s; "
                "post-download restore not armed", book_id, user_id,
            )
            return None
        log.info(
            "Kobo download of book %s carried no device headers; attributed "
            "to device_id=%s", book_id, device_id,
        )
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
    """Return the open (armed or pending) row for this device/book, or ``None``."""
    if device_id is None or book_id is None:
        return None
    return (
        ub.session.query(ub.KoboDeviceBookDownload)
        .filter(
            ub.KoboDeviceBookDownload.device_id == device_id,
            ub.KoboDeviceBookDownload.book_id == book_id,
            ub.KoboDeviceBookDownload.restore_state.in_(RESTORE_OPEN_STATES),
        )
        .one_or_none()
    )


def settle_pending_download(row, *, state, count=None):
    """Mark the row consumed. The caller owns the commit.

    An ``armed`` row is not consumed: the device has not downloaded yet, so
    the GET that follows the download must be served too. Only ``restored_*``
    are refreshed so the ledger shows the answer that was given.
    """
    row.restored_at = _now()
    row.restored_count = count
    if row.restore_state == RESTORE_ARMED and state == RESTORE_SERVED:
        return
    row.restore_state = state
