# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Share my place gates for the Kobo protocol (spec sections 11 and 12, 3a).

A Kobo is *resolved* when ``requires_kobo_auth`` tied the request to a
registered device. While ``share_place.gates_enforced`` is on for the
account, a resolved Kobo is either *checked* (it reads and writes the shared
KoboReadingState graph, arbitrated latest-wins through ``place_arbiter``) or
*unchecked* (it reads and writes only its own DeviceReadingPosition row).
Everything else takes today's path.

The shared bookmark's order is (client clock, server receipt) where the
receipt is ``KoboBookmark.server_modified_at``. The stored device clock is
never treated as a receipt.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import case, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .. import ub
from .place_arbiter import Report, report_wins
from .share_place import gates_enforced

CHECKED = "checked"
UNCHECKED = "unchecked"

# The Kobo wire's "never": a clock every device-held state is newer than.
EPOCH = datetime(1970, 1, 1)

_CAS_ATTEMPTS = 3


def _now():
    return datetime.now(timezone.utc)


def _naive_utc(value):
    if value.tzinfo is not None and value.utcoffset() is not None:
        value = value.astimezone(timezone.utc)
    return value.replace(tzinfo=None)


def effective_clock(client_at, received_at):
    """The clock ``report_wins`` orders a report by: its client clock, unless
    missing or more than five minutes past its own receipt (naive UTC)."""
    received = _naive_utc(received_at)
    if client_at is None:
        return received
    client = _naive_utc(client_at)
    return received if client > received + timedelta(minutes=5) else client


def kobo_place_mode(user_id, device_id, session=None):
    """Return CHECKED, UNCHECKED, or None for today's path.

    None when the Kobo is unresolved, the device is not this account's, or
    the rollout switch is off for the account.
    """
    if not device_id or not gates_enforced(user_id):
        return None
    s = session if session is not None else ub.session
    row = s.execute(
        select(ub.Device.user_id, ub.Device.share_place)
        .where(ub.Device.id == int(device_id))
    ).first()
    if row is None or int(row.user_id) != int(user_id):
        return None
    return CHECKED if row.share_place else UNCHECKED


# -- the device's own journal row --------------------------------------------

@dataclass(frozen=True)
class JournalOutcome:
    accepted: bool
    rehydrate_pending: bool


def _journal_report(row):
    """The report a journal row holds, or None if it holds none (a latch or
    nudge placeholder)."""
    if row is None:
        return None
    clock = row.client_modified_at or row.status_modified_at
    if clock is None and row.progress_percent is None and not row.location_value \
            and row.reading_status is None:
        return None
    return Report(clock, row.server_modified_at)


def record_own_report(*, device_id, book_id, client_at, place=None,
                      status=None, status_supplied=False, session=None):
    """Write one device report onto its own journal row, latest-wins.

    ``place`` is a dict of the DeviceReadingPosition place columns, or None
    when the report carried no bookmark. A report that loses the arbiter
    against the row changes nothing. ``rehydrate_needed`` is never touched.
    """
    s = session if session is not None else ub.session
    table = ub.DeviceReadingPosition.__table__
    device_id, book_id = int(device_id), int(book_id)
    for _ in range(_CAS_ATTEMPTS):
        row = s.execute(select(table).where(
            table.c.device_id == device_id, table.c.book_id == book_id,
        )).first()
        # An unacknowledged turn-on nudge guards a cover reset the same way
        # an armed re-download latch does.
        pending = bool(row is not None and row.rehydrate_needed) or _nudged(
            s, device_id, book_id)
        received_at = _now()
        if not report_wins(Report(client_at, received_at), _journal_report(row)):
            return JournalOutcome(False, pending)
        values = {"server_modified_at": received_at}
        if client_at is not None:
            values["client_modified_at"] = client_at
        if place is not None:
            values.update(place)
        if status_supplied:
            values["reading_status"] = status
            values["status_modified_at"] = client_at or received_at
        if row is None:
            result = s.execute(sqlite_insert(table).values(
                device_id=device_id, book_id=book_id, **values,
            ).on_conflict_do_nothing(index_elements=["device_id", "book_id"]))
        else:
            result = s.execute(update(table).where(
                table.c.id == row.id,
                table.c.server_modified_at == row.server_modified_at,
            ).values(**values))
        if result.rowcount == 1:
            return JournalOutcome(True, pending)
    return JournalOutcome(False, pending)


def _nudged(session, device_id, book_id):
    nudge = ub.KoboSharePlaceNudge
    return session.execute(select(nudge.id).where(
        nudge.device_id == device_id, nudge.book_id == book_id,
    )).first() is not None


def _kobo_ts(value):
    from ..kobo import convert_to_kobo_timestamp_string
    return convert_to_kobo_timestamp_string(value if value is not None else EPOCH)


def journal_state_response(book, row):
    """Render GET /state for an unchecked Kobo from its own journal row.

    With no report on the row, render a transient reading state that holds
    nothing: no CurrentBookmark and every clock at the epoch, so whatever the
    device holds locally wins. Nothing is persisted either way.
    """
    from ..kobo import _clean_progress, get_read_status_for_kobo
    report = _journal_report(row)
    if report is None:
        clock = status_clock = None
    else:
        clock = row.client_modified_at or row.status_modified_at
        status_clock = row.status_modified_at
    response = {
        "EntitlementId": book.uuid,
        "Created": _kobo_ts(book.timestamp),
        "LastModified": _kobo_ts(clock),
        "PriorityTimestamp": _kobo_ts(clock),
        "StatusInfo": {
            "LastModified": _kobo_ts(status_clock),
            "Status": get_read_status_for_kobo(SimpleNamespace(
                read_status=row.reading_status if report is not None else None)),
            "TimesStartedReading": 0,
        },
        # An unchecked Kobo's statistics are not stored (PR-NOTES); serving
        # an epoch-clocked empty block keeps the device's own.
        "Statistics": {"LastModified": _kobo_ts(None)},
    }
    if report is not None and (row.progress_percent is not None
                               or row.location_value):
        mark = {"LastModified": _kobo_ts(row.client_modified_at or clock)}
        if row.progress_percent is not None:
            mark["ProgressPercent"] = _clean_progress(row.progress_percent)
        if row.content_source_progress_percent is not None:
            mark["ContentSourceProgressPercent"] = _clean_progress(
                row.content_source_progress_percent)
        if row.location_value:
            mark["Location"] = {
                "Value": row.location_value,
                "Type": row.location_type,
                "Source": row.location_source,
            }
        response["CurrentBookmark"] = mark
    return response


# -- the shared bookmark ------------------------------------------------------

def shared_bookmark_report(progress_percent, location_value, last_modified,
                           server_modified_at):
    """The report the shared bookmark holds, or None if it was never written.

    A row with no receipt predates the receipt column and was not backfilled;
    its receipt is unknown, so it is ordered as received at the epoch (any
    receipt-checked report outranks it) rather than trusting its device clock.
    """
    if progress_percent is None and not location_value:
        return None
    return Report(last_modified, server_modified_at or EPOCH)


@dataclass(frozen=True)
class SharedWriteOutcome:
    accepted: bool
    percentage: object
    clock: object = None


def write_shared_bookmark(bookmark_id, *, client_at, progress_percent,
                          content_source_progress_percent=None,
                          content_source_supplied=False,
                          location=None, location_supplied=False,
                          block_lower_at_or_below=None, session=None):
    """Arbitrate one checked writer's place onto the shared Kobo bookmark.

    Latest wins by ``report_wins``: a deliberate backward move is accepted,
    an older report arriving later is not, and a client clock more than five
    minutes past its receipt is ordered by the receipt. The stored clock is
    the winning report's effective clock, so the reading-state feed clock
    stays monotonic and a later winner always moves the sync frontier.
    ``block_lower_at_or_below`` keeps today's armed-latch cover-reset guard.
    The write is a compare-and-swap on the receipt, retried on contention.
    """
    s = session if session is not None else ub.session
    mark = ub.KoboBookmark
    for _ in range(_CAS_ATTEMPTS):
        row = s.execute(select(
            mark.progress_percent, mark.location_value, mark.last_modified,
            mark.server_modified_at, mark.kobo_reading_state_id,
        ).where(mark.id == bookmark_id)).first()
        if row is None:
            return SharedWriteOutcome(False, None)
        received_at = _now()
        current = shared_bookmark_report(
            row.progress_percent, row.location_value, row.last_modified,
            row.server_modified_at)
        if not report_wins(Report(client_at, received_at), current):
            return SharedWriteOutcome(False, row.progress_percent, row.last_modified)
        if (block_lower_at_or_below is not None and progress_percent is not None
                and progress_percent <= block_lower_at_or_below
                and row.progress_percent is not None
                and row.progress_percent > progress_percent):
            return SharedWriteOutcome(False, row.progress_percent, row.last_modified)
        clock = effective_clock(client_at, received_at)
        values = {"last_modified": clock, "server_modified_at": received_at}
        if progress_percent is not None:
            values["progress_percent"] = progress_percent
            if progress_percent > 0:
                values["created_at"] = case(
                    (mark.created_at.is_(None), clock), else_=mark.created_at)
        if content_source_supplied:
            values["content_source_progress_percent"] = content_source_progress_percent
        if location_supplied:
            location = location or {}
            values.update({
                "location_value": location.get("Value"),
                "location_type": location.get("Type"),
                "location_source": location.get("Source"),
            })
        receipt = mark.server_modified_at
        result = s.execute(update(mark.__table__).where(
            mark.id == bookmark_id,
            receipt.is_(None) if row.server_modified_at is None
            else receipt == row.server_modified_at,
        ).values(**values))
        if result.rowcount != 1:
            continue
        if row.kobo_reading_state_id is not None:
            state = ub.KoboReadingState
            monotonic = case(
                (or_(state.last_modified.is_(None), state.last_modified < clock),
                 clock),
                else_=state.last_modified,
            )
            s.execute(update(state.__table__).where(
                state.id == row.kobo_reading_state_id,
            ).values(last_modified=monotonic, priority_timestamp=monotonic))
        stored = progress_percent if progress_percent is not None else row.progress_percent
        return SharedWriteOutcome(True, stored, clock)
    return SharedWriteOutcome(False, None)


# -- turning the box on -------------------------------------------------------

def refresh_share_place_nudges(device, *, session):
    """Arm the one-time turn-on emission for a Kobo whose box was turned on.

    For each book with a shared place, the device's own journal row goes
    through the arbiter against it; the book is armed only where the shared
    place winning). Nudges armed by an earlier turn-on are re-decided.
    Nothing is written to the shared rows, so no other Kobo is sent anything.

    Re-download repair latches armed while the box was off were never served
    (they carry the shared state, which was not this device's). They are
    disarmed here: the nudge is the arbiter's answer for each book, and a
    latch left armed would send the shared place where the device's own
    place is newer, then echo it a second time on acknowledgement.
    """
    if device.kind != "kobo":
        return 0
    clear_share_place_nudges(device.id, session=session)
    journal = ub.DeviceReadingPosition.__table__
    session.execute(update(journal).where(
        journal.c.device_id == int(device.id),
        journal.c.rehydrate_needed.is_(True),
    ).values(rehydrate_needed=False))
    state, mark = ub.KoboReadingState, ub.KoboBookmark
    shared = session.execute(
        select(state.book_id, mark.progress_percent, mark.location_value,
               mark.last_modified, mark.server_modified_at)
        .join(mark, mark.kobo_reading_state_id == state.id)
        .where(state.user_id == device.user_id)
    ).all()
    if not shared:
        return 0
    own = {
        row.book_id: row for row in session.execute(select(journal).where(
            journal.c.device_id == device.id,
            journal.c.book_id.in_([row.book_id for row in shared]),
        )).all()
    }
    armed = []
    for row in shared:
        report = shared_bookmark_report(
            row.progress_percent, row.location_value, row.last_modified,
            row.server_modified_at)
        if report is not None and report_wins(report, _journal_report(own.get(row.book_id))):
            armed.append(row.book_id)
    if not armed:
        return 0
    now = _now()
    session.execute(sqlite_insert(ub.KoboSharePlaceNudge.__table__).values([
        {"device_id": int(device.id), "book_id": int(book_id), "armed_at": now}
        for book_id in armed
    ]).on_conflict_do_nothing(index_elements=["device_id", "book_id"]))
    return len(armed)


def clear_share_place_nudges(device_id, *, session):
    nudge = ub.KoboSharePlaceNudge.__table__
    return session.execute(nudge.delete().where(
        nudge.c.device_id == int(device_id))).rowcount
