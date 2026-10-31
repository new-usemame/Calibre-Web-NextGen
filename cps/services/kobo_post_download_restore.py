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

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from werkzeug.wsgi import ClosingIterator

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

# Nickel's book file GET carries the auth token in its URL but no x-kobo-*
# headers, so on an account with two or more Kobos the download route cannot
# tell which one is fetching (#2724). The URL itself comes from that Kobo's own
# library/sync, which does carry the header, so sync names the device in it.
DOWNLOAD_DEVICE_PARAM = "device"


def download_url_device_tag(device_id):
    """The public id a download URL handed to Kobo ``device_id`` carries."""
    if device_id is None:
        return None
    device = ub.session.query(ub.Device).filter_by(id=int(device_id)).one_or_none()
    if device is None or device.kind != "kobo":
        return None
    return device.public_id


def tagged_download_device(user_id, public_id):
    """The account's active Kobo a download URL names, or None.

    Only the account's own active Kobos count: a URL edited to name any other
    device attributes nothing, and the download falls back to the heuristic.
    """
    if user_id is None or not public_id:
        return None
    device = ub.session.query(ub.Device).filter_by(
        public_id=str(public_id), user_id=int(user_id), kind="kobo", active=True,
    ).one_or_none()
    return device.id if device is not None else None



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
    device_id, _unambiguous = _resolve_download_attribution(
        user_id=user_id, book_id=book_id, log=log,
    )
    return device_id


def _resolve_download_attribution(*, user_id, book_id, log):
    """Keep the restore heuristic, and separately establish file-proof authority."""
    if user_id is None:
        return None, False
    try:
        user_devices = ub.session.query(ub.Device).filter(
            ub.Device.user_id == int(user_id),
            ub.Device.kind == "kobo",
        ).order_by(ub.Device.last_seen_at.desc(), ub.Device.id.desc()).all()
        active_devices = [device for device in user_devices if device.active]
        if not active_devices:
            return None, False
        now = _now()

        def recent(value, age, *, inclusive=True):
            if value is None:
                return False
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value >= now - age if inclusive else value > now - age

        candidates = {
            device.id for device in user_devices
            if recent(device.last_seen_at, timedelta(days=30))
        }
        active_ids = {device.id for device in active_devices}
        device_id = active_devices[0].id
        fresh_arms = set()
        if book_id is not None:
            armed_rows = (
                ub.session.query(ub.KoboDeviceBookDownload)
                .filter(
                    ub.KoboDeviceBookDownload.book_id == int(book_id),
                    ub.KoboDeviceBookDownload.restore_state == RESTORE_ARMED,
                    ub.KoboDeviceBookDownload.device_id.in_(
                        [device.id for device in user_devices],
                    ),
                )
                .order_by(ub.KoboDeviceBookDownload.downloaded_at.desc())
                .all()
            )
            restore_arms = [row for row in armed_rows if row.device_id in active_ids]
            if restore_arms:
                # Restore still follows the newest active arm, even if stale.
                device_id = restore_arms[0].device_id
            fresh_arms = {
                row.device_id for row in armed_rows
                if row.device_id in candidates
                and recent(row.downloaded_at, timedelta(minutes=15), inclusive=False)
            }
        # Inactive but recently synced Kobos remain possible senders. Neither
        # an old arm nor a different sole candidate proves the restore guess.
        unambiguous = candidates == {device_id} or fresh_arms == {device_id}
        return device_id, unambiguous
    except SQLAlchemyError:
        ub.session.rollback()
        log.warning(
            "Could not resolve the downloading Kobo for user_id=%s book_id=%s",
            user_id, book_id, exc_info=True,
        )
        return None, False


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
            # A restore announcement is not a file download. Invalidating
            # proof also survives an armed annotation GET returning empty.
            row.completed_at = None
            row.kepub_file_identity = None
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


def record_download(*, device_id, book_id, book_format, log, user_id=None,
                    completed=True):
    """Upsert the restore row after a download, or stage it before a response.

    The route stages annotation restoration at its historical point, then
    confirms file proof only after a full GET body has streamed to the end.
    Direct callers record a completed download by default.
    """
    if book_id is None:
        return None
    unambiguous = device_id is not None
    if device_id is None:
        device_id, unambiguous = _resolve_download_attribution(
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
        row.completed_at = row.downloaded_at if completed and unambiguous else None
        # An ambiguous restore attribution must never become exact-place proof,
        # even when the guessed device successfully receives the whole file.
        row.kepub_file_identity = None if unambiguous else ""
        if unambiguous and (book_format or "").lower() == "kepub":
            # Best-effort file proof is independent of annotation restoration.
            # Failure to prove it must not prevent the pending row being saved.
            try:
                from cps import calibre_db
                from .kobo_place_gate import _book_paths, _readable_kepub_identity
                book = calibre_db.get_book(int(book_id))
                if book is not None:
                    _epub, path = _book_paths(book)
                    row.kepub_file_identity = _readable_kepub_identity(path)
            except Exception:
                log.debug("Could not prove Kobo download file for book %s", book_id,
                          exc_info=True)
        if not completed and row.kepub_file_identity is None:
            # Distinguish a modern, uncompleted attempt from a pre-column
            # pending row that was recorded by a completed legacy download.
            row.kepub_file_identity = ""
        row.restore_state = RESTORE_PENDING
        row.restored_at = None
        row.restored_count = None
        from .kobo_removal import arm_own_restore
        arm_own_restore(device_id, book_id)
        ub.session.commit()
        if row.kepub_file_identity:
            from .kobo_resume import warm_alignment
            warm_alignment(book_id)
        return row
    except SQLAlchemyError:
        ub.session.rollback()
        log.warning(
            "Could not record Kobo download device_id=%s book_id=%s",
            device_id, book_id, exc_info=True,
        )
        return None


def confirm_download(*, download_id, downloaded_at, file_identity, kepub_path,
                     session, log):
    """Confirm this attempt's file proof without changing annotation restore.

    A failed or incomplete stream never reaches this seam. A replacement file, another
    download, or a restore announcement racing the response cannot inherit
    the earlier attempt's identity. No content/hash work runs on the request.
    """
    if not file_identity:
        return False
    from .kobo_place_gate import _readable_kepub_identity
    if file_identity != _readable_kepub_identity(kepub_path):
        return False
    table = ub.KoboDeviceBookDownload.__table__
    try:
        result = session.execute(update(table).where(
            table.c.id == download_id,
            table.c.downloaded_at == downloaded_at,
            table.c.kepub_file_identity == file_identity,
            table.c.completed_at.is_(None),
            table.c.restore_state != RESTORE_ARMED,
        ).values(completed_at=_now()))
        session.commit()
        confirmed = result.rowcount == 1
    except SQLAlchemyError:
        session.rollback()
        log.warning("Could not confirm Kobo file download proof for attempt %s",
                    download_id, exc_info=True)
        return False
    if confirmed:
        _re_send_shared_place(download_id, session=session, log=log)
    return confirmed


def _re_send_shared_place(download_id, *, session, log):
    """Arm the one-time re-send of the shared place for a proven download.

    Separate from the proof's own commit: failing here must never undo the
    proof, so it rolls back only its own work (#2688).
    """
    table = ub.KoboDeviceBookDownload.__table__
    try:
        row = session.execute(
            select(table.c.device_id, table.c.book_id).where(table.c.id == download_id)
        ).first()
        if row is not None:
            from .kobo_place_gate import arm_download_place_nudge
            arm_download_place_nudge(row.device_id, row.book_id, session=session)
        session.commit()
    except Exception:
        session.rollback()
        log.warning("Could not arm the shared place re-send for download %s",
                    download_id, exc_info=True)


def completed_download_body(body, *, application, bind, download_id,
                            downloaded_at, file_identity, kepub_path, headers, log):
    """Confirm only natural exhaustion matching the declared byte count.

    No context or session is held while yielding bytes. The independent
    session opened at exhaustion cannot commit another request's work.
    ClosingIterator closes the underlying file even if streaming never starts.
    """
    content_length = headers.get("Content-Length", type=int)
    if (content_length is None or content_length <= 0
            or "X-Sendfile" in headers or "X-Accel-Redirect" in headers):
        # Delegated transfers and lengthless streams cannot prove a full file.
        return body

    def stream():
        yielded = 0
        for chunk in body:
            yielded += len(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
            yield chunk
        if yielded != content_length:
            return
        try:
            with application.app_context(), sessionmaker(bind=bind)() as owned:
                confirm_download(download_id=download_id, downloaded_at=downloaded_at,
                                 file_identity=file_identity, kepub_path=kepub_path,
                                 session=owned, log=log)
        except Exception:
            # Proof is best effort; a persistence failure must not fail a body
            # already delivered. The owned session is rolled back and closed.
            log.warning("Kobo download completion failed for attempt %s",
                        download_id, exc_info=True)

    return ClosingIterator(stream(), getattr(body, "close", None))


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
