# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression tests for HandleStateRequest PUT-path robustness.

Three independent bugs surfaced during a Kobo subsystem audit on
2026-05-10. None are large but each maps to a real user-visible
failure mode:

1. push_reading_state_to_hardcover called outside the request_bookmark
   guard -> 500 when Kobo PUTs a Statistics-only or StatusInfo-only
   payload (which devices legitimately do during a reading session).
2. request_statistics["RemainingTimeMinutes"] / ["SpentReadingMinutes"]
   accessed unconditionally -> KeyError + entire PUT rolled back when
   the device sends partial stats. The bookmark + status_info updates
   in the same request are lost.
3. get_statistics_response uses falsy-check on integer fields -> drops
   legitimate 0 values from the response, same class of bug as the
   ProgressPercent=0 fix in PR #126.

These are pinned via source inspection because HandleStateRequest
needs Flask request + auth context not available at unit scope.
"""

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub


def _make_statistics(spent_reading_minutes=None,
                     remaining_time_minutes=None,
                     last_modified=None):
    return SimpleNamespace(
        spent_reading_minutes=spent_reading_minutes,
        remaining_time_minutes=remaining_time_minutes,
        last_modified=last_modified or datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.mark.unit
class TestStatisticsResponseFalsyCheck:
    def test_zero_spent_reading_minutes_present(self):
        from cps.kobo import get_statistics_response
        resp = get_statistics_response(_make_statistics(
            spent_reading_minutes=0,
            remaining_time_minutes=42,
        ))
        assert resp["SpentReadingMinutes"] == 0
        assert resp["RemainingTimeMinutes"] == 42

    def test_zero_remaining_time_minutes_present(self):
        from cps.kobo import get_statistics_response
        resp = get_statistics_response(_make_statistics(
            spent_reading_minutes=15,
            remaining_time_minutes=0,
        ))
        assert resp["SpentReadingMinutes"] == 15
        assert resp["RemainingTimeMinutes"] == 0

    def test_none_omitted_from_response(self):
        from cps.kobo import get_statistics_response
        resp = get_statistics_response(_make_statistics(
            spent_reading_minutes=None,
            remaining_time_minutes=None,
        ))
        assert "SpentReadingMinutes" not in resp
        assert "RemainingTimeMinutes" not in resp


@pytest.mark.unit
def test_rejected_clock_defeats_the_real_onupdate_default():
    """Changing progress must not let SQLAlchemy manufacture a newer clock."""
    from cps.kobo import _apply_kobo_last_modified

    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    old_clock = datetime(2020, 1, 2)
    state = ub.KoboReadingState(user_id=1, book_id=2)
    state.current_bookmark = ub.KoboBookmark(
        progress_percent=1.0, last_modified=old_clock,
    )
    session.add(state)
    session.commit()

    state.current_bookmark.progress_percent = 2.0
    _apply_kobo_last_modified(state.current_bookmark, None)
    session.commit()

    assert state.current_bookmark.progress_percent == 2.0
    assert state.current_bookmark.last_modified == old_clock
    session.close()


@pytest.mark.unit
class TestHandleStateRequestPartialStatistics:
    """When the device sends Statistics with only one of the two
    minute fields (which it legitimately does between full updates),
    we must not crash. Pre-fix, missing RemainingTimeMinutes raised
    KeyError, the outer try/except returned 400, and the bookmark +
    status_info updates in the same PUT were lost."""

    def test_uses_get_for_spent_reading_minutes(self):
        from cps.kobo import HandleStateRequest
        src = inspect.getsource(HandleStateRequest)
        assert 'request_statistics.get("SpentReadingMinutes")' in src, (
            "PUT handler must use .get() for SpentReadingMinutes so "
            "partial-stats payloads don't roll back the whole PUT."
        )

    def test_uses_get_for_remaining_time_minutes(self):
        from cps.kobo import HandleStateRequest
        src = inspect.getsource(HandleStateRequest)
        assert 'request_statistics.get("RemainingTimeMinutes")' in src, (
            "PUT handler must use .get() for RemainingTimeMinutes so "
            "partial-stats payloads don't roll back the whole PUT."
        )


@pytest.mark.unit
class TestPushReadingStateToHardcoverGuard:
    """push_reading_state_to_hardcover used to be called
    unconditionally with request_bookmark['ProgressPercent']. If the
    device PUTs Statistics-only or StatusInfo-only payloads (no
    CurrentBookmark), request_bookmark is None and the call raises
    TypeError -> 500 to the device, which then retries forever.
    Even when CurrentBookmark IS present, it might omit ProgressPercent
    -> KeyError, same crash."""

    def test_call_is_guarded_by_request_bookmark_truthy(self):
        from cps.kobo import HandleStateRequest
        src = inspect.getsource(HandleStateRequest)
        assert (
            'if request_bookmark and request_bookmark.get("ProgressPercent")'
            in src
        ), (
            "push_reading_state_to_hardcover call must be guarded "
            "behind `if request_bookmark and request_bookmark.get(...)` "
            "so Statistics-only and StatusInfo-only PUTs don't crash."
        )
