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

from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .. import ub
from .place_arbiter import Report, report_wins
from .share_place import gates_enforced

CHECKED = "checked"
UNCHECKED = "unchecked"

# The Kobo wire's "never": a clock every device-held state is newer than.
EPOCH = datetime(1970, 1, 1)

_CAS_ATTEMPTS = 3

# How far past the server's clock a stored clock may sit and still be one a
# device could honestly have sent (place_arbiter's skew allowance).
SKEW = timedelta(minutes=5)

# The smallest step that survives the sync token's float-seconds round trip.
FEED_STEP = timedelta(milliseconds=1)


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
    place_accepted: bool = False


def _journal_report(row):
    """The report a journal row holds, or None if it holds none (a latch or
    nudge placeholder)."""
    if row is None:
        return None
    clock = _latest(row.client_modified_at, row.status_modified_at)
    if clock is None and row.progress_percent is None and not row.location_value \
            and row.reading_status is None:
        return None
    return Report(clock, row.server_modified_at)


# Spec section 13: each journal observation (place, status) is ordered by
# PR 1's Report(raw client clock, that observation's own receipt), built at
# read time so report_wins applies the skew clamp and the receipt tiebreak.
# Nothing normalized is stored, so rows the switch-off path wrote (raw
# clocks) read the same way. Writing one observation never touches the
# other's pair.

def _journal_place_report(row):
    """The place observation a journal row holds, or None if it holds none."""
    if row is None or (row.progress_percent is None and not row.location_value):
        return None
    return Report(row.client_modified_at, row.server_modified_at)


# Spec section 13a: every writer that sets a status sets its receipt in the
# same statement, and the startup migration freezes a receipt onto any older
# status row. A status row still without one is read with main's key,
# Report(status_modified_at, status_modified_at); the place's receipt moves
# with every place write, so it never orders a status.

def _status_receipt(row):
    return row.status_received_at or row.status_modified_at


def _journal_status_report(row):
    """The status observation a journal row holds, or None if it holds none
    (or holds one with no clock at all, which main also treats as absent)."""
    if row is None or (row.reading_status is None
                       and row.status_modified_at is None):
        return None
    receipt = _status_receipt(row)
    if receipt is None:
        return None
    return Report(row.status_modified_at, receipt)


def _place_absent(table):
    return and_(table.c.progress_percent.is_(None),
                or_(table.c.location_value.is_(None), table.c.location_value == ""))


def _status_absent(table):
    return and_(table.c.reading_status.is_(None), table.c.status_modified_at.is_(None))


def _same(column, value):
    return column.is_(None) if value is None else column == value


def _latest(*clocks):
    present = [_naive_utc(clock) for clock in clocks if clock is not None]
    return max(present) if present else None


def record_own_report(*, device_id, book_id, client_at, place=None,
                      status=None, status_supplied=False, session=None):
    """Write one device report onto its own journal row, latest-wins.

    ``place`` is a dict of the DeviceReadingPosition place columns, or None
    when the report carried no bookmark. Place and status are separate
    observations, each arbitrated by its own (raw clock, own receipt) pair:
    a report can carry the newest place and an older status, and only the
    fields and pair that won are written, guarded by that pair. A report where nothing wins changes
    nothing. ``rehydrate_needed`` is never touched.
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
        candidate = Report(client_at, received_at)
        place_wins = place is not None and report_wins(
            candidate, _journal_place_report(row))
        status_wins = status_supplied and report_wins(
            candidate, _journal_status_report(row))
        if not (place_wins or status_wins):
            return JournalOutcome(False, pending)
        values, guard = {}, []
        if place_wins:
            values.update(place, client_modified_at=client_at,
                          server_modified_at=received_at)
            if row is not None:
                # Section 13a: an empty place guards its emptiness, not just
                # the pair, which a status-only first report shares.
                guard += [_same(table.c.client_modified_at, row.client_modified_at),
                          _same(table.c.server_modified_at, row.server_modified_at)]
                if _journal_place_report(row) is None:
                    guard.append(_place_absent(table))
        if status_wins:
            values.update(reading_status=status, status_modified_at=client_at,
                          status_received_at=received_at)
            if row is not None:
                guard += [_same(table.c.status_modified_at, row.status_modified_at),
                          _same(table.c.status_received_at, row.status_received_at)]
                if _journal_status_report(row) is None:
                    guard.append(_status_absent(table))
        if row is None:
            # The row's receipt column is required; a status-only first
            # report leaves the place empty, so it orders nothing.
            values.setdefault("server_modified_at", received_at)
            result = s.execute(sqlite_insert(table).values(
                device_id=device_id, book_id=book_id, **values,
            ).on_conflict_do_nothing(index_elements=["device_id", "book_id"]))
        else:
            result = s.execute(update(table).where(
                table.c.id == row.id, *guard).values(**values))
        if result.rowcount == 1:
            return JournalOutcome(True, pending, place_wins)
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
        clock = status_clock = place_clock = None
    else:
        place_clock = row.client_modified_at or (
            row.server_modified_at if _journal_place_report(row) else None)
        status_clock = row.status_modified_at or (
            _status_receipt(row) if _journal_status_report(row) else None)
        clock = _latest(place_clock, status_clock)
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
        mark = {"LastModified": _kobo_ts(place_clock)}
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
            # Only now that this write moved the receipt: a lost CAS or
            # exhausted retries leave the legacy child clocks untouched.
            _settle_legacy_future_clocks(
                row.kobo_reading_state_id, row.server_modified_at, session=s)
            advance_feed_clock(row.kobo_reading_state_id, clock, session=s)
        stored = progress_percent if progress_percent is not None else row.progress_percent
        return SharedWriteOutcome(True, stored, clock)
    return SharedWriteOutcome(False, None)


def advance_feed_clock(reading_state_id, clock, *, session):
    """Move a KoboReadingState's feed clock for an accepted shared change.

    The new clock is ``clock`` when that is later than the stored one, and
    otherwise one step past the stored one: every accepted change must pass
    the frontier a Kobo already holds, including a same-second receipt
    tiebreak and a change below a legacy future-dated clock. Returns the
    clock written (naive UTC).
    """
    state = ub.KoboReadingState
    stored = session.execute(select(state.last_modified).where(
        state.id == reading_state_id)).scalar()
    target = _naive_utc(clock)
    if stored is not None and _naive_utc(stored) >= target:
        target = _naive_utc(stored) + FEED_STEP
    session.execute(update(state.__table__).where(
        state.id == reading_state_id,
    ).values(last_modified=target, priority_timestamp=target))
    return target


def _settle_legacy_future_clocks(reading_state_id, baseline, *, session):
    """Freeze legacy future-dated shared status and statistics clocks at the
    baseline they are ordered by (the bookmark receipt captured before this
    write), once this write's compare-and-swap has moved that receipt.

    A legacy fast device can have left them further in the future than any
    honest clock; until replaced they are ordered at the shared bookmark's
    receipt (capped at the upgrade by the migration, see
    ``skew_ordered_clock``). Writing that receipt into them before an
    accepted shared place moves it keeps their ordering, so a place-only
    write cannot re-time them. Core updates: the feed clock is untouched.
    Reached only from ``write_shared_bookmark`` (enforced, checked writers).
    """
    if baseline is None:
        return
    stats, read, state = ub.KoboStatistics, ub.ReadBook, ub.KoboReadingState
    cutoff = _naive_utc(_now()) + SKEW
    session.execute(update(stats.__table__).where(
        stats.kobo_reading_state_id == reading_state_id,
        stats.last_modified > cutoff,
    ).values(last_modified=baseline))
    owner = session.execute(select(state.user_id, state.book_id).where(
        state.id == reading_state_id)).first()
    if owner is not None:
        session.execute(update(read.__table__).where(
            read.user_id == owner.user_id, read.book_id == owner.book_id,
            read.last_modified > cutoff,
        ).values(last_modified=baseline))


def future_skewed(stored, now=None):
    """True when a stored clock is further past the server's clock than any
    honest device could be: such a clock cannot outrank a report."""
    if stored is None:
        return False
    return _naive_utc(stored) > future_cutoff(now)


def future_cutoff(now=None):
    """Stored clocks after this are further in the future than any honest
    device clock (naive UTC)."""
    return _naive_utc(now or _now()) + SKEW


def skew_ordered_clock(stored, baseline):
    """The clock a stored status or statistics clock is ordered by: itself,
    unless future-skewed, then ``baseline`` (the shared receipt)."""
    if stored is not None and future_skewed(stored):
        return baseline
    return stored


def own_place_newer(device_id, book_id, *, session=None):
    """True when this Kobo's own journal report is strictly newer than the
    shared place: the shared state is then older than what the device holds
    and must not be sent to it."""
    s = session if session is not None else ub.session
    journal = ub.DeviceReadingPosition.__table__
    own = s.execute(select(journal).where(
        journal.c.device_id == int(device_id),
        journal.c.book_id == int(book_id),
    )).first()
    own_report = _journal_place_report(own)
    if own_report is None:
        return False
    state, mark = ub.KoboReadingState, ub.KoboBookmark
    shared = s.execute(
        select(mark.progress_percent, mark.location_value, mark.last_modified,
               mark.server_modified_at)
        .join(state, mark.kobo_reading_state_id == state.id)
        .join(ub.Device, ub.Device.user_id == state.user_id)
        .where(ub.Device.id == int(device_id), state.book_id == int(book_id))
    ).first()
    shared_report = None if shared is None else shared_bookmark_report(
        shared.progress_percent, shared.location_value, shared.last_modified,
        shared.server_modified_at)
    return shared_report is not None and report_wins(own_report, shared_report)


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
        if report is not None and report_wins(report, _journal_place_report(own.get(row.book_id))):
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
