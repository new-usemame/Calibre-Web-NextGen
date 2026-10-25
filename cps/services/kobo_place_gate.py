# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Share my place gates for the Kobo protocol (spec sections 11–14b, 3a/3b).

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

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .. import ub
from .place_arbiter import Report, report_wins
from .share_place import gates_enforced
from .place_journal import (
    _journal_place_report, _journal_status_report, _status_receipt, _same,
    write_report,
)

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

def _latest(*clocks):
    present = [_naive_utc(clock) for clock in clocks if clock is not None]
    return max(present) if present else None


def record_own_report(*, device_id, book_id, client_at, place=None,
                      status=None, status_supplied=False, session=None):
    """Journal a Kobo report through the common CAS writer, retaining its latch."""
    s = session if session is not None else ub.session
    row = s.execute(select(ub.DeviceReadingPosition.rehydrate_needed).where(
        ub.DeviceReadingPosition.device_id == int(device_id),
        ub.DeviceReadingPosition.book_id == int(book_id),
    )).first()
    pending = bool(row is not None and row.rehydrate_needed) or _nudged(
        s, device_id, book_id)
    from . import kobo_removal
    if place and pending and kobo_removal.own_restore_pending(device_id, book_id, session=s):
        previous = s.query(ub.DeviceReadingPosition).filter_by(
            device_id=device_id, book_id=book_id).one_or_none()
        progress = place.get('progress_percent')
        from ..kobo import KOB0_COVER_RESET_PROGRESS_EPSILON
        if (progress is not None and 0 <= progress <= KOB0_COVER_RESET_PROGRESS_EPSILON
                and previous is not None and previous.progress_percent is not None
                and previous.progress_percent > progress):
            place = None
            status_supplied = False
    outcome = write_report(
        device_id=device_id, book_id=book_id, client_at=client_at,
        place=place, status=status, status_supplied=status_supplied,
        received_at=_now(), session=s,
    )
    if outcome.place_accepted and place and place.get('location_type') == 'KoboSpan':
        from .kobo_resume import warm_alignment
        warm_alignment(book_id)
    return JournalOutcome(outcome.accepted, pending, outcome.place_accepted)


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
    received_at: object = None


def write_shared_bookmark(bookmark_id, *, client_at, progress_percent,
                          content_source_progress_percent=None,
                          content_source_supplied=False,
                          location=None, location_supplied=False,
                          block_lower_at_or_below=None, shared_from_koreader=False,
                          span_kepub_identity=None, received_at=None, session=None):
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
            mark.shared_from_koreader, mark.location_source, mark.location_type,
            mark.content_source_progress_percent,
        ).where(mark.id == bookmark_id)).first()
        if row is None:
            return SharedWriteOutcome(False, None)
        received_at = received_at or _now()
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
        values = {"last_modified": clock, "server_modified_at": received_at,
                  # A percentage-only report retains the stored Location's
                  # origin; it cannot turn a converted span into a native one.
                  "shared_from_koreader": (shared_from_koreader or
                                           (row.shared_from_koreader and not location_supplied)),
                  "span_kepub_identity": span_kepub_identity}
        if shared_from_koreader and not row.shared_from_koreader:
            # Preserve the last native Location for a Kobo with no own report.
            # It must never be handed the newly converted, ineligible span.
            values.update(native_location_value=row.location_value,
                          native_location_source=row.location_source,
                          native_location_type=row.location_type,
                          native_progress_percent=row.progress_percent,
                          native_content_source_progress_percent=row.content_source_progress_percent)
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
        return SharedWriteOutcome(True, stored, clock, received_at)
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
    if active_echo_guard(device_id, book_id, session=s) is not None:
        # A journaled echo is fresh, but it is not a newer reading place.
        return False
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


def arm_download_place_nudge(device_id, book_id, *, session):
    """Re-send a book's shared place once to a Kobo that proved it downloaded it.

    Nickel treats every (re-)download as "forget the position I had", so it
    opens the book at the cover. A shared place sent before the download is
    gone from the device, but the device's reading-state cursor is already
    past it, so nothing would send it again until the account's place changed
    (#2688). Called once the download is proven complete: the same proof that
    makes the delivered span exact.

    Armed only where the turn-on nudge would be: the box is on and the shared
    place beats this Kobo's own report. For a Kobo with an older report of
    its own, the own-place restore armed at download time sends the same
    place, and the sync drops the nudge's copy as a duplicate; the nudge is
    what covers a Kobo with no report, or one past that restore's window.
    Returns whether a nudge was armed.
    """
    device = session.execute(
        select(ub.Device.user_id, ub.Device.kind, ub.Device.share_place)
        .where(ub.Device.id == int(device_id))
    ).first()
    if (device is None or device.kind != "kobo" or not device.share_place
            or not gates_enforced(device.user_id)):
        return False
    state, mark = ub.KoboReadingState, ub.KoboBookmark
    shared = session.execute(
        select(mark.progress_percent, mark.location_value, mark.last_modified,
               mark.server_modified_at)
        .join(state, mark.kobo_reading_state_id == state.id)
        .where(state.user_id == device.user_id, state.book_id == int(book_id))
    ).first()
    shared_report = None if shared is None else shared_bookmark_report(
        shared.progress_percent, shared.location_value, shared.last_modified,
        shared.server_modified_at)
    if shared_report is None:
        return False
    journal = ub.DeviceReadingPosition.__table__
    own = session.execute(select(journal).where(
        journal.c.device_id == int(device_id),
        journal.c.book_id == int(book_id),
    )).first()
    if not report_wins(shared_report, _journal_place_report(own)):
        return False
    session.execute(sqlite_insert(ub.KoboSharePlaceNudge.__table__).values(
        device_id=int(device_id), book_id=int(book_id), armed_at=_now(),
    ).on_conflict_do_nothing(index_elements=["device_id", "book_id"]))
    return True


def clear_share_place_nudges(device_id, *, session):
    nudge = ub.KoboSharePlaceNudge.__table__
    return session.execute(nudge.delete().where(
        nudge.c.device_id == int(device_id))).rowcount


# -- exact delivery and the inexact echo guard (spec sections 14a, 14b) --------

def _book_paths(book):
    from ..annotations import _book_format_path
    try:
        return _book_format_path(book, "EPUB"), _book_format_path(book, "KEPUB")
    except (OSError, TypeError, AttributeError):
        # A missing/unavailable library cannot establish an exact place.
        return None, None


def _file_identity(path):
    stat = os.stat(path)
    return f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}"


def koreader_span(book_id, document, progress):
    """Convert only an XPointer known to address the current library EPUB.

    Return the span and the KEPUB identity used in the conversion. Replacing
    either file during conversion invalidates the result.
    """
    from .. import calibre_db
    from .koreader_position import file_digest, is_xpointer
    from .kepub_alignment import xpointer_to_span
    from .parallel import run_blocking
    if not is_xpointer(progress) or not isinstance(document, str):
        return None, None
    book = calibre_db.get_book(int(book_id))
    if book is None:
        return None, None
    epub, kepub = _book_paths(book)
    if not epub or not kepub:
        return None, None
    def convert():
        try:
            before = (_file_identity(epub), _file_identity(kepub))
            if file_digest(epub) != document.lower():
                return None, None
            found = xpointer_to_span(epub, kepub, progress)
            if not found or before != (_file_identity(epub), _file_identity(kepub)):
                return None, None
            source, span = found
            return {"Source": source, "Value": span, "Type": "KoboSpan"}, before[1]
        except (OSError, TypeError, ValueError):
            return None, None
    return run_blocking(convert)


def clear_echo_guard(device_id, book_id=None, *, session=None):
    s = session if session is not None else ub.session
    table = ub.KoboPlaceEchoGuard.__table__
    terms = [table.c.device_id == int(device_id)]
    if book_id is not None:
        terms.append(table.c.book_id == int(book_id))
    s.execute(table.delete().where(*terms))


def active_echo_guard(device_id, book_id, *, session=None):
    s = session if session is not None else ub.session
    table = ub.KoboPlaceEchoGuard.__table__
    return s.execute(select(table).where(
        table.c.device_id == int(device_id), table.c.book_id == int(book_id),
        table.c.armed_at > _naive_utc(_now()) - timedelta(days=7),
    )).first()


def _readable_kepub_identity(path):
    """Cheap identity of a nonempty accessible file, stable across the check."""
    if not path:
        return None
    try:
        before = _file_identity(path)
        with open(path, "rb") as stream:
            if not stream.read(1):
                return None
        return before if before == _file_identity(path) else None
    except (OSError, TypeError):
        return None


def completed_kepub_identity(device_id, book, *, session=None):
    """Proof this device completed a download of the current readable KEPUB.

    New downloads freeze completed_at/file identity in record_download.
    Restore arming invalidates that proof. Pre-column pending/served rows
    are the historical completed-download states; only those retain the old
    format/mtime proof. An armed-to-empty annotation response is never proof.
    """
    from .kobo_post_download_restore import RESTORE_ARMED, RESTORE_PENDING, RESTORE_SERVED
    s = session if session is not None else ub.session
    row = s.query(ub.KoboDeviceBookDownload).filter_by(
        device_id=int(device_id), book_id=int(book.id)).one_or_none()
    if (row is None or (row.book_format or "").lower() != "kepub"
            or row.restore_state == RESTORE_ARMED):
        return None
    _epub, path = _book_paths(book)
    identity = _readable_kepub_identity(path)
    if identity is None:
        return None
    if row.completed_at is not None:
        if row.kepub_file_identity != identity:
            return None
        completed = row.completed_at
    elif (row.kepub_file_identity is None
          and row.restore_state in (RESTORE_PENDING, RESTORE_SERVED)):
        completed = row.downloaded_at
    else:
        return None
    try:
        written = datetime.fromtimestamp(os.stat(path).st_mtime, timezone.utc)
        if completed is not None and _naive_utc(completed) >= _naive_utc(written):
            return identity if identity == _readable_kepub_identity(path) else None
    except OSError:
        pass
    return None


def native_bookmark_response(bookmark, response):
    """Historical native Location for an unresolved reader of a converted row."""
    if not bookmark.shared_from_koreader:
        return response
    response = dict(response)
    if bookmark.native_location_value:
        response["Location"] = {
            "Value": bookmark.native_location_value,
            "Source": bookmark.native_location_source,
            "Type": bookmark.native_location_type,
        }
    else:
        response.pop("Location", None)
    return response


def delivery_bookmark(book, bookmark, response, device_id):
    """Prove exact delivery, otherwise arm and serve only a held native place.

    Conversion freezes the KEPUB identity at write time. Native Kobo writes
    freeze the sender's completed-download proof at that same seam. Receiver
    proof is rechecked here, including replay; a later sender download cannot
    validate an earlier report. No held Location means no shared bookmark.
    """
    own = ub.session.query(ub.DeviceReadingPosition).filter_by(
        device_id=int(device_id), book_id=int(book.id)).one_or_none()
    receiver_identity = completed_kepub_identity(device_id, book)
    if (receiver_identity is not None and bookmark.location_value
            and bookmark.location_source and bookmark.location_type == "KoboSpan"
            and bookmark.span_kepub_identity == receiver_identity):
        clear_echo_guard(device_id, book.id)
        return response
    if own is not None and own.location_value:
        held = own
    elif bookmark.shared_from_koreader:
        held = SimpleNamespace(
            location_value=bookmark.native_location_value,
            location_source=bookmark.native_location_source,
            location_type=bookmark.native_location_type,
            progress_percent=bookmark.native_progress_percent,
            content_source_progress_percent=bookmark.native_content_source_progress_percent)
    else:
        held = bookmark
    # No shared place has ever been written, so no inexact delivery to arm.
    if bookmark.progress_percent is None and not bookmark.location_value:
        return None
    table = ub.KoboPlaceEchoGuard.__table__
    values = dict(device_id=int(device_id), book_id=int(book.id),
                  location_value=held.location_value, location_source=held.location_source,
                  progress_percent=held.progress_percent,
                  content_source_progress_percent=held.content_source_progress_percent,
                  shared_received_at=bookmark.server_modified_at, armed_at=_now())
    insert = sqlite_insert(table).values(**values)
    ub.session.execute(insert.on_conflict_do_update(
        index_elements=["device_id", "book_id"],
        set_={key: value for key, value in values.items()
              if key not in ("device_id", "book_id")},
        where=~_same(table.c.shared_received_at, bookmark.server_modified_at),
    ))
    if not held.location_value:
        return None
    response = dict(response)
    response["Location"] = {"Value": held.location_value,
                            "Source": held.location_source,
                            "Type": held.location_type}
    return response


def _span_ordinal(value):
    match = re.fullmatch(r"kobo\.([0-9]{1,9})\.([0-9]{1,9})", value or "")
    return tuple(map(int, match.groups())) if match else None


def _first_spine_source(book):
    from .kepub_alignment import _kepub
    _epub, path = _book_paths(book)
    if not path:
        return None
    kepub = _kepub(path)
    return kepub.documents[0].member if kepub and kepub.documents else None


def is_inexact_echo(device_id, book, place):
    """Judge before journaling. A real move or expired guard clears the arm."""
    guard = active_echo_guard(device_id, book.id)
    if guard is None:
        # Do not take SQLite's write lock for the overwhelmingly common case
        # of no guard: concurrent bookmark writers must still reach the CAS.
        table = ub.KoboPlaceEchoGuard.__table__
        expired = ub.session.execute(select(table.c.id).where(
            table.c.device_id == int(device_id), table.c.book_id == int(book.id),
        )).first()
        if expired is not None:
            clear_echo_guard(device_id, book.id)
        return False
    location = place.get("Location") or {}
    value, source = location.get("Value"), location.get("Source")
    progress = place.get("ProgressPercent")
    echo = not value or (value == guard.location_value and source == guard.location_source)
    if not echo and progress == 0:
        echo = source == _first_spine_source(book)
        if not echo and not guard.location_value:
            from .kepub_alignment import _kepub
            _epub, path = _book_paths(book)
            kepub = _kepub(path) if path else None
            first = next(((doc.member, doc.ids[0]) for doc in kepub.documents
                          if doc.ids), None) if kepub else None
            echo = first == (source, value)
    incoming, armed = _span_ordinal(value), _span_ordinal(guard.location_value)
    if (not echo and source == guard.location_source and incoming and armed
            and incoming <= armed and progress is not None
            and guard.progress_percent is not None):
        # No measured chapter page step is available: use the spec's
        # conservative fallback of at most one book percentage point.
        delta = guard.progress_percent - progress
        echo = 0 <= delta <= 1
    if not echo:
        clear_echo_guard(device_id, book.id)
    return echo
