# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Time-only arbitration for shared reading place and status reports.

Protocol gates can compare observations here without consulting the database
or their location in a book. A deliberate backward move is therefore eligible
to win, while an older client report cannot win just by arriving later.
Each report's effective time is fixed by its own receipt time, so arbitration
does not change as wall-clock time passes.
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


def report_wins(candidate: Report, current: Optional[Report]) -> bool:
    """Return whether candidate is strictly newer, with receipt as tiebreak.

    Each report uses its client clock unless it is missing or more than five
    minutes after that report's own server receipt, in which case it uses the
    receipt time. Compare (effective time, receipt time) after UTC normalization.
    Exact ties keep the current report; the first report always wins.
    """
    if current is None:
        return True

    def key(report: Report):
        server_at = _utc(report.server_at)
        client_at = _utc(report.client_at) if report.client_at is not None else None
        if client_at is None or client_at > server_at + timedelta(minutes=5):
            client_at = server_at
        return client_at, server_at

    return key(candidate) > key(current)
