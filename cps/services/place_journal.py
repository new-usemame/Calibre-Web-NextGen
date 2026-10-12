# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Protocol-neutral latest-wins journal for device place and status reports.

Each observation keeps its raw clock and its own receipt. Compare-and-swap
checks both clocks and the absence of an observation before a first write.
Caller owns the transaction; protocol-specific latches remain with the caller.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .. import ub
from .place_arbiter import Report, report_wins

_CAS_ATTEMPTS = 3


@dataclass(frozen=True)
class JournalWriteOutcome:
    accepted: bool
    place_accepted: bool = False
    status_accepted: bool = False


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


def write_report(*, device_id, book_id, client_at, place=None,
                 status=None, status_supplied=False, received_at=None, session=None):
    """Write one device report onto its own journal row, latest-wins.

    ``place`` is a dict of the DeviceReadingPosition place columns, or None
    when the report carried no bookmark. Place and status are separate
    observations, each arbitrated by its own (raw clock, own receipt) pair:
    a report can carry the newest place and an older status, and only the
    fields and pair that won are written, guarded by that pair. A report where
    nothing wins changes nothing. ``rehydrate_needed`` is never touched.
    """
    s = session if session is not None else ub.session
    table = ub.DeviceReadingPosition.__table__
    device_id, book_id = int(device_id), int(book_id)
    received_at = received_at or datetime.now(timezone.utc)
    for _ in range(_CAS_ATTEMPTS):
        row = s.execute(select(table).where(
            table.c.device_id == device_id, table.c.book_id == book_id,
        )).first()
        candidate = Report(client_at, received_at)
        place_wins = place is not None and report_wins(
            candidate, _journal_place_report(row))
        status_wins = status_supplied and report_wins(
            candidate, _journal_status_report(row))
        if not (place_wins or status_wins):
            return JournalWriteOutcome(False)
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
            return JournalWriteOutcome(True, place_wins, status_wins)
    return JournalWriteOutcome(False)
