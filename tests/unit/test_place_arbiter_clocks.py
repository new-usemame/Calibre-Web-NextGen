# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Clock representation and strict boundary checks for the pure arbiter."""

from datetime import datetime, timedelta, timezone

import pytest

from cps.services.place_arbiter import Report, report_wins


pytestmark = pytest.mark.unit
NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


def test_five_minute_boundary_after_receipt_is_inclusive_but_next_instant_uses_receipt():
    current = Report(NOW, NOW)
    received = NOW - timedelta(minutes=1)
    boundary = received + timedelta(minutes=5)
    assert report_wins(Report(boundary, received), current)
    assert not report_wins(Report(boundary + timedelta(microseconds=1), received), current)


def test_naive_database_utc_and_offset_clocks_compare_as_instants():
    stored = Report(NOW.replace(tzinfo=None), NOW.replace(tzinfo=None))
    offset = timezone(timedelta(hours=-4))
    same = NOW.astimezone(offset)
    assert not report_wins(Report(same, same), stored)
    assert report_wins(Report(same, same + timedelta(seconds=1)), stored)
