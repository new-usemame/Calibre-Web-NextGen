# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Is the scheduled-task window open right now? (#2551)

On startup the scheduler kicks the scheduled tasks off immediately when the
window is already open, so a wrong answer either runs maintenance outside the
administrator's window or skips it until the next day. The window opens every
day at a whole local hour and lasts a number of minutes of real time.
"""

import datetime
from zoneinfo import ZoneInfo

import pytest

from cps.schedule import schedule_window_active

NY = ZoneInfo("America/New_York")
UTC = datetime.timezone.utc


def local(year, month, day, hour, minute, fold=0, zone=NY):
    return datetime.datetime(year, month, day, hour, minute, tzinfo=zone, fold=fold)


@pytest.mark.parametrize("now, start, duration, expected", [
    # An ordinary day.
    (local(2026, 7, 1, 2, 59), 3, 60, False),
    (local(2026, 7, 1, 3, 0), 3, 60, True),
    (local(2026, 7, 1, 3, 59), 3, 60, True),
    (local(2026, 7, 1, 4, 0), 3, 60, False),
    # A window that opens late in the evening is still open after midnight.
    (local(2026, 7, 1, 23, 30), 23, 90, True),
    (local(2026, 7, 2, 0, 15), 23, 90, True),
    (local(2026, 7, 2, 0, 30), 23, 90, False),
    # Spring forward (2026-03-08, 02:00 EST becomes 03:00 EDT). A window set
    # for the skipped hour opens when the clocks resume.
    (local(2026, 3, 8, 3, 10), 2, 60, True),
    (local(2026, 3, 8, 4, 0), 2, 60, False),
    # A window spanning the jump lasts its real two hours, to 04:00 EDT.
    (local(2026, 3, 8, 3, 30), 1, 120, True),
    (local(2026, 3, 8, 4, 0), 1, 120, False),
    # Fall back (2026-11-01, 02:00 EDT becomes 01:00 EST). The 01:00 window
    # opened at the first 01:00 and closed an hour later; the repeated hour
    # is not a second window.
    (local(2026, 11, 1, 1, 30, fold=0), 1, 60, True),
    (local(2026, 11, 1, 1, 30, fold=1), 1, 60, False),
    # A two-hour window from midnight ends at the second 01:00, not at 02:00.
    (local(2026, 11, 1, 1, 15, fold=0), 0, 120, True),
    (local(2026, 11, 1, 1, 15, fold=1), 0, 120, False),
    # The clock arriving in another zone is read in that zone's hours.
    (datetime.datetime(2026, 7, 1, 7, 30, tzinfo=UTC), 3, 60, False),
    (datetime.datetime(2026, 7, 1, 3, 30, tzinfo=UTC), 3, 60, True),
])
def test_window_is_open_exactly_when_expected(now, start, duration, expected):
    assert schedule_window_active(now, start, duration) is expected


def test_a_naive_clock_is_refused():
    with pytest.raises(ValueError):
        schedule_window_active(datetime.datetime(2026, 7, 1, 3, 30), 3, 60)
