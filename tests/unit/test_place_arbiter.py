# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance tests for the one latest-wins place arbiter (Share my place, PR 1).

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 3 criteria 3
and 5, section 8 "One arbiter". The arbiter compares two reports by time only;
it has no notion of where in the book either report is, so a deliberate
backward move wins whenever it is the newer report, and a stale echo (a device
re-sending a place it had before it received a newer one) loses because its
device time is older.
"""

from datetime import datetime, timedelta, timezone

import pytest


pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


def _arbiter():
    from cps.services import place_arbiter
    return place_arbiter


def _report(client_at, server_at):
    return _arbiter().Report(client_at=client_at, server_at=server_at)


def _minutes(offset):
    return NOW + timedelta(minutes=offset)


def test_first_report_for_a_book_always_wins():
    arbiter = _arbiter()
    assert arbiter.report_wins(_report(_minutes(-30), _minutes(-30)), None, now=NOW) is True
    assert arbiter.report_wins(_report(None, _minutes(-30)), None, now=NOW) is True


def test_a_deliberate_backward_move_wins_because_it_is_newer():
    # Kobo read to chapter 10 at -60 min. Later, on the Kindle, the reader goes
    # back to chapter 5 on purpose at -5 min. The Kindle's report is the newer
    # one, so it must win even though it is "behind" in the book.
    arbiter = _arbiter()
    chapter_10_on_kobo = _report(_minutes(-60), _minutes(-60))
    chapter_5_on_kindle = _report(_minutes(-5), _minutes(-5))
    assert arbiter.report_wins(chapter_5_on_kindle, chapter_10_on_kobo, now=NOW) is True
    # And the arbiter is not "newest server receipt wins": the older report
    # never displaces the newer one.
    assert arbiter.report_wins(chapter_10_on_kobo, chapter_5_on_kindle, now=NOW) is False


def test_a_stale_echo_loses_even_though_the_server_receives_it_last():
    # Kobo reported its place at -30 min. The Kindle then reported a newer place
    # at -10 min (the current shared place). The Kobo, before applying the newer
    # place, re-sends its old one: same device time (-30), but received now.
    arbiter = _arbiter()
    kindle_newer = _report(_minutes(-10), _minutes(-10))
    kobo_echo = _report(_minutes(-30), NOW)
    assert arbiter.report_wins(kobo_echo, kindle_newer, now=NOW) is False


def test_an_older_device_time_loses():
    arbiter = _arbiter()
    current = _report(_minutes(-10), _minutes(-10))
    assert arbiter.report_wins(_report(_minutes(-11), _minutes(-1)), current, now=NOW) is False


def test_equal_device_times_are_broken_by_server_receive_time():
    arbiter = _arbiter()
    same = _minutes(-10)
    earlier_receipt = _report(same, _minutes(-9))
    later_receipt = _report(same, _minutes(-8))
    assert arbiter.report_wins(later_receipt, earlier_receipt, now=NOW) is True
    assert arbiter.report_wins(earlier_receipt, later_receipt, now=NOW) is False


def test_a_missing_device_time_falls_back_to_server_receive_time():
    arbiter = _arbiter()
    current = _report(_minutes(-10), _minutes(-10))
    assert arbiter.report_wins(_report(None, _minutes(-5)), current, now=NOW) is True
    assert arbiter.report_wins(_report(None, _minutes(-15)), current, now=NOW) is False
    # The same fallback applies to the stored report.
    stored_without_device_time = _report(None, _minutes(-10))
    assert arbiter.report_wins(_report(_minutes(-5), _minutes(-5)),
                               stored_without_device_time, now=NOW) is True
    assert arbiter.report_wins(_report(_minutes(-15), _minutes(-1)),
                               stored_without_device_time, now=NOW) is False


def test_a_candidate_clock_far_in_the_future_is_judged_by_its_receive_time():
    # A device whose clock runs an hour fast must not beat a genuinely newer
    # report: its device time is ignored and its receive time (-2 min) is used.
    arbiter = _arbiter()
    current = _report(_minutes(-1), _minutes(-1))
    skewed = _report(_minutes(60), _minutes(-2))
    assert arbiter.report_wins(skewed, current, now=NOW) is False


def test_a_stored_future_clock_cannot_hold_the_place_forever():
    # The stored report came from a device whose clock ran a day fast; it was
    # received 10 minutes ago. An honest report made now must displace it.
    arbiter = _arbiter()
    stored_skewed = _report(_minutes(24 * 60), _minutes(-10))
    honest = _report(NOW, NOW)
    assert arbiter.report_wins(honest, stored_skewed, now=NOW) is True


def test_small_clock_drift_within_five_minutes_is_trusted():
    # +4 min is inside the tolerance, so the device time stands and beats a
    # report made at -1 min, although this report was received at -2 min.
    arbiter = _arbiter()
    current = _report(_minutes(-1), _minutes(-1))
    assert arbiter.report_wins(_report(_minutes(4), _minutes(-2)), current, now=NOW) is True
    # +6 min is beyond it, so the receive time (-2) decides and loses.
    assert arbiter.report_wins(_report(_minutes(6), _minutes(-2)), current, now=NOW) is False


def test_now_defaults_to_the_real_clock():
    # Without an explicit now, a device time a day ahead of the real clock is
    # still treated as skewed.
    arbiter = _arbiter()
    real_now = datetime.now(timezone.utc)
    current = _report(real_now - timedelta(minutes=1), real_now - timedelta(minutes=1))
    skewed = _report(real_now + timedelta(days=1), real_now - timedelta(minutes=2))
    assert arbiter.report_wins(skewed, current) is False
