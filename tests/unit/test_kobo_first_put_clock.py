# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A Kobo's first report for a book is not lost to the server's own clock.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 12
("First-Kobo-PUT stamp bug"); ships as its own ``### Fixed`` item, outside the
Share my place rollout switch, so these run with the switch off.

The bug: PUT /state for a book with no reading-state rows first creates them
stamped with the server's current time. The device's own LastModified (a few
seconds behind the server, as every real report is) then counts as older
than rows that hold nothing, so its StatusInfo is dropped, the stored clock is
the server's instead of the device's (GET no longer echoes it, which is what
keeps Nickel from raising its "newer progress" prompt), and the next writer
with a newer but still past clock is rejected.
"""

from datetime import timedelta

import pytest

from tests.unit.kobo_state_world import (  # noqa: F401  (fixtures)
    _kts, _now, kw, read_column_class,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def gates_off(monkeypatch):
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "0")


def test_a_kobos_first_report_for_a_book_is_accepted_whole(kw):
    book = kw.books[0]
    clock = _now() - timedelta(seconds=5)

    kw.put(kw.a, book, 40.0, clock, status="Finished")

    shared = kw.shared(book)
    assert shared["read"][0] == kw.ub.ReadBook.STATUS_FINISHED
    assert shared["bookmark"][0] == 40.0
    assert shared["bookmark"][2] == "kobo.40.1"
    served = kw.get(kw.a, book)
    assert served["LastModified"] == _kts(clock)
    assert served["CurrentBookmark"]["LastModified"] == _kts(clock)
    assert served["StatusInfo"]["Status"] == "Finished"


def test_the_next_writer_with_a_newer_clock_is_accepted(kw):
    book = kw.books[0]
    first = _now() - timedelta(seconds=5)
    second = _now() - timedelta(seconds=2)
    kw.put(kw.a, book, 40.0, first, status="Reading")

    kw.put(kw.b, book, 25.0, second, status="Finished")

    shared = kw.shared(book)
    assert shared["bookmark"][0] == 25.0
    assert shared["bookmark"][2] == "kobo.25.1"
    assert shared["read"][0] == kw.ub.ReadBook.STATUS_FINISHED
    assert kw.get(kw.b, book)["LastModified"] == _kts(second)
