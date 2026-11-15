# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-Kobo removal capture holds and bounded post-download own-place restore.

A PATCH receipt proves an upload, not that Nickel has sent every local note.
Hold counts advance only when a reliable sync would otherwise remove the book;
replay of an unacknowledged response does not advance them. Request transactions
own every write here.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy.dialects.sqlite import insert

from .. import ub
from .device_reading_position import timestamp_is_newer


def now():
    return datetime.now(timezone.utc)


def record_activity(device_id, book_id):
    if device_id is None:
        return
    statement = insert(ub.KoboDeviceBookRemoval).values(
        device_id=device_id, book_id=book_id, activity_received_at=now())
    ub.session.execute(statement.on_conflict_do_update(
        index_elements=['device_id', 'book_id'],
        set_={'activity_received_at': statement.excluded.activity_received_at}))


def record_upload(device_id, book_id):
    if device_id is None:
        return
    statement = insert(ub.KoboDeviceBookRemoval).values(
        device_id=device_id, book_id=book_id, annotation_uploaded_at=now())
    ub.session.execute(statement.on_conflict_do_update(
        index_elements=['device_id', 'book_id'],
        set_={'annotation_uploaded_at': statement.excluded.annotation_uploaded_at,
              'hold_syncs': 0}))


def capture_holds(device_id, candidates, cap, log):
    """Return withheld ids; reset a previous hold when scope admits it again."""
    if device_id is None:
        return set()
    rows = ub.session.query(ub.KoboDeviceBookRemoval).filter_by(device_id=device_id).all()
    held = set()
    for row in rows:
        if row.book_id not in candidates or not timestamp_is_newer(
                row.activity_received_at, row.annotation_uploaded_at):
            row.hold_syncs = 0
            continue
        if row.hold_syncs < cap:
            row.hold_syncs += 1
            held.add(row.book_id)
        else:
            log.warning('Kobo removal capture hold exhausted device_id=%s book_id=%s syncs=%s',
                        device_id, row.book_id, row.hold_syncs)
    return held


def preserve_deletion_capture(session, user_id, book_id, book_uuid):
    """Save receipts before permanent purge, keyed by the delivered UUID."""
    rows = session.query(ub.KoboDeviceBookRemoval).join(
        ub.Device, ub.Device.id == ub.KoboDeviceBookRemoval.device_id).filter(
            ub.Device.user_id == user_id, ub.Device.kind == 'kobo',
            ub.KoboDeviceBookRemoval.book_id == book_id).all()
    for row in rows:
        if not timestamp_is_newer(row.activity_received_at, row.annotation_uploaded_at):
            continue
        statement = insert(ub.KoboDeviceDeletionCapture).values(
            device_id=row.device_id, book_uuid=str(book_uuid), book_id=book_id,
            activity_received_at=row.activity_received_at,
            annotation_uploaded_at=row.annotation_uploaded_at,
            hold_syncs=row.hold_syncs)
        session.execute(statement.on_conflict_do_nothing(
            index_elements=['device_id', 'book_uuid']))


def deletion_capture_holds(device_id, book_uuid, cap, log):
    if device_id is None:
        return False
    row = ub.session.get(ub.KoboDeviceDeletionCapture, (device_id, book_uuid))
    if row is None or not timestamp_is_newer(row.activity_received_at, row.annotation_uploaded_at):
        return False
    if row.hold_syncs < cap:
        row.hold_syncs += 1
        return True
    log.warning('Kobo removal capture hold exhausted device_id=%s book_id=%s syncs=%s',
                device_id, row.book_id, row.hold_syncs)
    return False


def arm_own_restore(device_id, book_id):
    """A known download may reset its own journal; restore for at most 15 min."""
    position = ub.session.query(ub.DeviceReadingPosition).filter_by(
        device_id=device_id, book_id=book_id).one_or_none()
    if position is None or position.progress_percent is None:
        return
    statement = insert(ub.KoboDeviceBookRemoval).values(
        device_id=device_id, book_id=book_id,
        own_restore_until=now() + timedelta(minutes=15))
    ub.session.execute(statement.on_conflict_do_update(
        index_elements=['device_id', 'book_id'],
        set_={'own_restore_until': statement.excluded.own_restore_until}))
    position.rehydrate_needed = True
    position.rehydrate_armed_at = now()


def own_restore_pending(device_id, book_id, *, session=None):
    if device_id is None:
        return False
    s = session if session is not None else ub.session
    row = s.get(ub.KoboDeviceBookRemoval, (device_id, book_id))
    return row is not None and timestamp_is_newer(row.own_restore_until, now())


def finish_own_restore(device_id, book_id):
    row = ub.session.get(ub.KoboDeviceBookRemoval, (device_id, book_id))
    if row is not None:
        row.own_restore_until = None


def record_removal_ack(device_id, book_id):
    statement = insert(ub.KoboDeviceBookRemoval).values(
        device_id=device_id, book_id=book_id, removal_acknowledged=True)
    ub.session.execute(statement.on_conflict_do_update(
        index_elements=['device_id', 'book_id'],
        set_={'removal_acknowledged': True, 'legacy_marker_delivery': False,
              'own_restore_until': None, 'hold_syncs': 0}))


def record_delivery_ack(device_id, book_ids):
    if book_ids:
        ub.session.query(ub.KoboDeviceBookRemoval).filter(
            ub.KoboDeviceBookRemoval.device_id == device_id,
            ub.KoboDeviceBookRemoval.book_id.in_(book_ids),
        ).update({'removal_acknowledged': False, 'legacy_marker_delivery': False}, synchronize_session=False)


def remember_legacy_markers(device_id, book_ids):
    for book_id in book_ids:
        statement = insert(ub.KoboDeviceBookRemoval).values(
            device_id=device_id, book_id=book_id, legacy_marker_delivery=True)
        ub.session.execute(statement.on_conflict_do_update(
            index_elements=['device_id', 'book_id'], set_={'legacy_marker_delivery': True}))
