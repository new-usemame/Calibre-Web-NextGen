# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance tests for the two findings of the third adversarial review of
"Share my place" PR 3a (state/kindle-kids/legs/share-place-pr3/REVIEW-sol-3.md,
reviewed at c3b778d17). Both are about one observation's ordering time
moving because a *different* observation on the same row was written.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, sections 3, 11
and 12.

1. An unchecked Kobo's journal orders place and status each by its own
   observation. A too-fast clock is ordered at that observation's receipt,
   and a later write of the other field does not re-time it: a delayed but
   newer report of the first field still wins. Both directions, each with a
   control that has no intervening write.
2. Legacy shared status and statistics carrying a day-fast clock are ordered
   at the capped upgrade baseline until they themselves are replaced. A
   checked place-only report does not move that baseline: a checked
   status/statistics report dated after the baseline lands. A report dated
   before the baseline still changes nothing.

Clocks are real: Kobo clocks are whole seconds, so the sequences in (1) wait
for the server's receipt to move past them.
"""

import time
from datetime import timedelta

import pytest

from tests.unit.kobo_state_world import (  # noqa: F401  (fixtures)
    _kts, _now, kw, read_column_class, second_after_receipts,
)
from tests.unit.test_share_place_kobo_review_findings import _raw_put

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "1")


def _report(clock, *, percent=None, status=None, statistics=None):
    return {"LastModified": _kts(clock),
            "CurrentBookmark": (None if percent is None
                                else {"ProgressPercent": percent}),
            "Statistics": statistics,
            "StatusInfo": None if status is None else {"Status": status}}


def _wait_until(moment):
    while _now() < moment:
        time.sleep(0.05)


# == 1. Journal: one field's write does not re-time the other =================

@pytest.mark.parametrize("intervening", [True, False],
                         ids=["other-field-written", "control"])
def test_a_newer_status_beats_a_fast_status_after_a_place_only_report(
        kw, intervening):
    """Status-only Reading with a clock a day fast (ordered at its receipt,
    T); a place-only 40% received at T+3s; then a delayed status-only
    Finished dated T+2s, newer than the fast status's T."""
    book = kw.books[0]
    _raw_put(kw, kw.a, book, _report(_now() + timedelta(days=1), status="Reading"))
    # T: the fast status's receipt (read back, not assumed to be the wall
    # clock before the PUT; on a loaded worker it lands seconds later).
    base = second_after_receipts(kw, kw.a, book)
    if intervening:
        _wait_until(base + timedelta(seconds=3))
        _raw_put(kw, kw.a, book, _report(base + timedelta(seconds=3), percent=40))

    _raw_put(kw, kw.a, book,
             _report(base + timedelta(seconds=2), status="Finished"))

    assert kw.get(kw.a, book)["StatusInfo"]["Status"] == "Finished"


@pytest.mark.parametrize("intervening", [True, False],
                         ids=["other-field-written", "control"])
def test_a_newer_place_beats_a_fast_place_after_a_status_only_report(
        kw, intervening):
    """Place-only 70% with a clock a day fast (ordered at its receipt, T); a
    status-only Reading received at T+3s; then a delayed honest 20% dated
    T+2s, newer than the fast place's T."""
    book = kw.books[0]
    _raw_put(kw, kw.a, book, _report(_now() + timedelta(days=1), percent=70))
    # T: the fast place's receipt (read back, not assumed to be the wall
    # clock before the PUT; on a loaded worker it lands seconds later).
    base = second_after_receipts(kw, kw.a, book)
    if intervening:
        _wait_until(base + timedelta(seconds=3))
        _raw_put(kw, kw.a, book,
                 _report(base + timedelta(seconds=3), status="Reading"))

    _raw_put(kw, kw.a, book, _report(base + timedelta(seconds=2), percent=20))

    assert kw.get(kw.a, book)["CurrentBookmark"]["ProgressPercent"] == 20


# == 2. Shared legacy recovery baseline =======================================

@pytest.fixture
def legacy_reading(kw):
    """Shared 70% / Reading / statistics 11 minutes, every clock a day fast;
    the migration capped the bookmark receipt two minutes before now. Kobo A
    is checked."""
    book = kw.books[0]
    base = _now()
    kw.seed_shared(book, 70, base + timedelta(days=1),
                   status=kw.ub.ReadBook.STATUS_IN_PROGRESS)
    kw.session.execute(kw.ub.KoboBookmark.__table__.update().values(
        last_modified=base + timedelta(days=1),
        server_modified_at=base - timedelta(minutes=2)))
    kw.session.commit()
    kw.share(kw.a, True)
    return book, base


FINISHED_STATS = {"SpentReadingMinutes": 99, "RemainingTimeMinutes": 0}


@pytest.mark.parametrize("place_only_first", [True, False],
                         ids=["place-only-written", "control"])
def test_a_finished_after_the_legacy_baseline_lands_despite_a_place_only_report(
        kw, legacy_reading, place_only_first):
    """A checked place-only 40% is received now; a checked status/statistics
    report dated a minute ago (after the baseline) says Finished, 99 minutes."""
    book, base = legacy_reading
    if place_only_first:
        _raw_put(kw, kw.a, book, _report(base, percent=40))
        assert kw.shared(book)["bookmark"][0] == 40

    _raw_put(kw, kw.a, book, _report(base - timedelta(minutes=1),
                                     status="Finished",
                                     statistics=FINISHED_STATS))

    after = kw.shared(book)
    assert after["read"][0] == kw.ub.ReadBook.STATUS_FINISHED
    assert after["statistics"][:2] == (99, 0)


def test_a_report_older_than_the_legacy_baseline_still_changes_nothing(
        kw, legacy_reading):
    book, base = legacy_reading
    _raw_put(kw, kw.a, book, _report(base, percent=40))
    before = kw.shared(book)

    _raw_put(kw, kw.a, book, _report(base - timedelta(hours=1),
                                     status="Finished",
                                     statistics=FINISHED_STATS))

    after = kw.shared(book)
    assert after["read"] == before["read"]
    assert after["statistics"] == before["statistics"]
