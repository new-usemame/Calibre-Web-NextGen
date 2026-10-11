# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Time-only arbitration for shared reading place and status reports.

Protocol gates can compare observations here without consulting the database
or their location in a book. A deliberate backward move is therefore eligible
to win, while an older client report cannot win just by arriving later.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass(frozen=True)
class Report:
    client_at: Optional[datetime]
    server_at: datetime


def _utc(value: datetime) -> datetime:
    """SQLite's naive timestamps represent UTC; normalize aware clocks too."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def report_wins(candidate: Report, current: Optional[Report], *,
                now: Optional[datetime] = None) -> bool:
    """Return whether candidate is strictly newer, with receipt as tiebreak.

    Missing client clocks and clocks more than five minutes ahead of ``now``
    use their server receipt instead. Apply that rule to both reports using
    the same UTC instant. Exact ties keep the current report; the first report
    always wins. Callers may inject ``now`` for deterministic comparisons.
    """
    if current is None:
        return True
    comparison_at = _utc(now if now is not None else datetime.now(timezone.utc))
    future_limit = comparison_at + timedelta(minutes=5)

    def key(report: Report):
        server_at = _utc(report.server_at)
        client_at = _utc(report.client_at) if report.client_at is not None else None
        if client_at is None or client_at > future_limit:
            client_at = server_at
        return client_at, server_at

    return key(candidate) > key(current)
