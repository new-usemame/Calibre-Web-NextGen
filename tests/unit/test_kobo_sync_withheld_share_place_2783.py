# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2783: a Kobo sync that holds back reading states says so, and why.

A user's DEBUG capture showed ``changed states: 2`` followed by an empty
response (``Content: b'[]'``) and a summary line with every counter at zero.
Share my place had withheld both states, but nothing in the log said whether
the Kobo's box read as off or its own place was newer, so the capture could
not tell a misconfigured device row from an arbitration decision.

The one-line ``Kobo Sync summary`` now names the Kobo's Share my place mode
and counts the books whose shared reading state was withheld, split by
reason. These run through the real ``HandleSyncRequest`` body over the
in-memory world in ``kobo_state_world``.
"""

import logging
from datetime import timedelta

import pytest

from tests.unit.kobo_state_world import (  # noqa: F401  (fixtures)
    _now, _states, kw, read_column_class,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "1")


def _summary(caplog):
    lines = [r.getMessage() for r in caplog.records
             if r.getMessage().startswith("Kobo Sync summary:")]
    assert lines, "no Kobo Sync summary was logged"
    return lines[-1]


def test_a_kobo_with_its_box_off_logs_each_withheld_book(kw, caplog):
    one, two = kw.books[0], kw.books[1]
    kw.seed_shared(one, 20.0, _now() - timedelta(hours=2))
    kw.seed_shared(two, 40.0, _now() - timedelta(hours=2))
    kw.share(kw.b, True)
    kw.settle(kw.a)
    kw.settle(kw.b)
    kw.put(kw.b, one, 60.0, _now() - timedelta(minutes=6))
    kw.put(kw.b, two, 70.0, _now() - timedelta(minutes=5))
    caplog.set_level(logging.DEBUG, logger="cps.kobo")

    response = kw.sync(kw.a)

    assert _states(response) == {}
    line = _summary(caplog)
    assert "share_place mode=unchecked withheld box_off=2 own_newer=0" in line


def test_a_checked_kobo_whose_own_place_is_newer_logs_own_newer(kw, caplog):
    book = kw.books[0]
    now = _now()
    kw.seed_shared(book, 60.0, now - timedelta(minutes=90))
    kw.put(kw.a, book, 15.0, now - timedelta(minutes=10))  # A read on, box off
    kw.settle(kw.a)
    kw.share(kw.a, True)
    kw.settle(kw.a)
    kw.share(kw.b, True)
    kw.settle(kw.b)
    kw.put(kw.b, book, 70.0, now - timedelta(minutes=30))  # newer than shared
    assert kw.shared(book)["bookmark"][0] == 70.0
    caplog.set_level(logging.DEBUG, logger="cps.kobo")

    response = kw.sync(kw.a)

    assert book.uuid not in _states(response)
    line = _summary(caplog)
    assert "share_place mode=checked withheld box_off=0 own_newer=1" in line


def test_a_checked_kobo_that_is_sent_the_place_logs_nothing_withheld(kw, caplog):
    book = kw.books[0]
    kw.seed_shared(book, 20.0, _now() - timedelta(hours=2))
    kw.share(kw.a, True)
    kw.share(kw.b, True)
    kw.settle(kw.a)
    kw.settle(kw.b)
    kw.put(kw.b, book, 60.0, _now() - timedelta(minutes=5))
    caplog.set_level(logging.DEBUG, logger="cps.kobo")

    response = kw.sync(kw.a)

    assert [s["CurrentBookmark"]["ProgressPercent"]
            for s in _states(response)[book.uuid]] == [60]
    line = _summary(caplog)
    assert "share_place mode=checked withheld box_off=0 own_newer=0" in line
