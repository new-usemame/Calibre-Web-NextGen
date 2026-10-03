# Copyright (C) 2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Conditional classification writes for automatic reading-progress reports."""

from datetime import datetime, timezone

from flask import g, has_request_context
from sqlalchemy import and_, case, func, or_, update

from .. import ub


def update_automatic_read_status(book_read, status, *, observed_clock=None,
                                 require_newer_clock=False, touch_unchanged=False,
                                 changed_only=False):
    """Apply automatic state only while the stored row is not explicitly paused.

    The pause and optional device-clock comparisons belong to the UPDATE, not
    the session's potentially stale identity map. Counters and timestamps are
    computed from the same persisted row. A rejected write changes none of
    these fields; callers continue updating independent positions/statistics.
    Caller owns the transaction and must flush new rows before calling.
    """
    table = ub.ReadBook.__table__
    fields = ("read_status", "last_modified", "times_started_reading",
              "last_time_started_reading", "read_status_choice_at")
    if require_newer_clock and observed_clock is None:
        ub.session.expire(book_read, fields)
        return False

    clock = observed_clock or datetime.now(timezone.utc)
    changed = table.c.read_status != status
    starting = and_(changed, status == ub.ReadBook.STATUS_IN_PROGRESS)
    conditions = [
        table.c.id == book_read.id,
        table.c.read_status.notin_((ub.ReadBook.STATUS_DID_NOT_FINISH,
                                  ub.ReadBook.STATUS_ON_HOLD)),
    ]
    if require_newer_clock:
        conditions.append(or_(table.c.last_modified.is_(None),
                              table.c.last_modified < clock))
    if changed_only:
        conditions.append(changed)

    # Explicitly supply last_modified even for unchanged statuses, defeating
    # the ORM column's onupdate default. Paused rows never match the statement.
    result = ub.session.execute(update(table).where(*conditions).values(
        read_status=status,
        last_modified=clock if touch_unchanged else case(
            (changed, clock), else_=table.c.last_modified),
        times_started_reading=case(
            (starting, func.coalesce(table.c.times_started_reading, 0) + 1),
            else_=table.c.times_started_reading),
        last_time_started_reading=case(
            (starting, datetime.now(timezone.utc)),
            else_=table.c.last_time_started_reading),
    ))
    ub.session.expire(book_read, fields)
    accepted = result.rowcount == 1
    changed_clock = False
    if accepted and not (touch_unchanged or changed_only):
        stored_clock = book_read.last_modified
        changed_clock = (stored_clock is not None and
                         stored_clock.replace(tzinfo=None) == clock.replace(tzinfo=None))
    if accepted and (touch_unchanged or changed_only or changed_clock):
        # Core updates bypass the ORM's normal parent-feed hook. Preserve that
        # notification without conflating the feed clock with the choice clock.
        state = book_read.kobo_reading_state
        if state is not None:
            feed_clock = (getattr(g, "kobo_reading_state_lm", None)
                          if has_request_context() else None) or clock
            state.last_modified = feed_clock
            state.priority_timestamp = feed_clock
    return accepted
