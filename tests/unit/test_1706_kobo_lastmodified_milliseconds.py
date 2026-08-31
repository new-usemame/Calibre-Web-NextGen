# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A Kobo LastModified with milliseconds must not fail the whole sync (#1706).

25e16f83 began parsing the device's ``LastModified`` with
``strptime(value, "%Y-%m-%dT%H:%M:%SZ")`` so the GET response could mirror it
back and avoid a "newer progress" popup. A Libra Colour on firmware 5.10.226356
sends ``2026-08-17T17:56:53.254Z`` -- with milliseconds -- and strptime raised
ValueError, which the caller's except turned into ``abort(400)``. The whole
reading-state sync failed, and the error text blamed a missing ``ReadingStates``
key, which was not the problem.

Reported with a full payload capture by a user on v4.1.37.
"""
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.unit


def _parse(value):
    from cps.kobo import parse_kobo_timestamp
    return parse_kobo_timestamp(value)


def test_milliseconds_are_accepted():
    """The exact string from the #1706 report."""
    got = _parse("2026-08-17T17:56:53.254Z")
    assert got == datetime(2026, 8, 17, 17, 56, 53, 254000, tzinfo=timezone.utc)


def test_whole_seconds_still_work():
    """The shape the original strptime handled must not regress."""
    got = _parse("2026-08-17T17:56:53Z")
    assert got == datetime(2026, 8, 17, 17, 56, 53, tzinfo=timezone.utc)


def test_microseconds_are_accepted():
    got = _parse("2026-08-17T17:56:53.254321Z")
    assert got == datetime(2026, 8, 17, 17, 56, 53, 254321, tzinfo=timezone.utc)


def test_explicit_offset_is_normalised_to_utc():
    got = _parse("2026-08-17T19:56:53.254+02:00")
    assert got == datetime(2026, 8, 17, 17, 56, 53, 254000, tzinfo=timezone.utc)


def test_naive_timestamp_is_treated_as_utc():
    got = _parse("2026-08-17T17:56:53")
    assert got == datetime(2026, 8, 17, 17, 56, 53, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", [None, "", "   ", "not a date", 12345, {}, "2026-13-45T99:99:99Z"])
def test_unparseable_returns_none_instead_of_raising(value):
    """The caller falls back to now(); it must never abort the sync (#1706)."""
    assert _parse(value) is None


def test_result_is_always_timezone_aware():
    for value in ("2026-08-17T17:56:53.254Z", "2026-08-17T17:56:53", "2026-08-17T19:56:53+02:00"):
        got = _parse(value)
        assert got is not None and got.tzinfo is not None
