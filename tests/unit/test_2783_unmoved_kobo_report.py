# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2783 item 2: a Kobo report that has not moved does not replace a newer
place from another reader.

Nickel stamps every PUT /state with the time it sends it. A Kobo that went to
sleep with a book open reports, on waking and before it syncs, the same place
it last reported, with a fresh clock. Latest-wins then let that clock beat a
place another reader saved in between (OBSERVED by the reporter on v4.1.62:
the web reader saved 31.78% at 21:37:45 and the Kobo's unchanged kobo.74.1
replaced it at 21:38:10).

A checked Kobo's report whose place equals the place it last reported, when
another reader has written the shared place since, is acknowledged but moves
nothing, so the Kobo's next sync serves it the newer place. A report that did
move (forward or back) still wins.

Real book, real kosync HTTP, real Kobo handlers (``share_place_span_world``).
"""

from datetime import timedelta

import pytest

from tests.unit.share_place_span_world import (  # noqa: F401  (fixture)
    _now, after_now, span_location, span_rows, sw,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "1")


@pytest.fixture
def rows():
    found = span_rows()
    return {"kobo": found[56], "kobo-moved": found[80], "koreader": found[300]}


def _setup(sw, rows):
    """A checked KOReader and a checked Kobo holding the library's KEPUB.
    The Kobo reported its place an hour ago; KOReader then read further on."""
    sw.koreader("koreader-1", "KOReader", checked=True)
    kobo = sw.kobo_device("Kobo", checked=True)
    sw.download(kobo)
    place = rows["kobo"]
    sw.put(kobo, place["source"], place["span"], 10.0, _now() - timedelta(hours=1))
    sw.settle(kobo)
    sw.push(rows["koreader"]["xpointer"], 40, client_at=after_now(2))
    assert sw.pull()[1] == rows["koreader"]["xpointer"]
    return kobo


def test_unmoved_kobo_report_keeps_the_newer_place(sw, rows):
    kobo = _setup(sw, rows)
    place = rows["kobo"]

    # The Kobo wakes and reports the same place, clocked after KOReader's save.
    sw.put(kobo, place["source"], place["span"], 10.0, after_now(5))

    percent, progress = sw.pull()
    assert progress == rows["koreader"]["xpointer"]
    assert percent == pytest.approx(40, abs=0.01)
    # And the Kobo itself is offered KOReader's place, not its own old one.
    assert sw.get(kobo)["CurrentBookmark"]["ProgressPercent"] == pytest.approx(40, abs=0.01)
    assert sw.synced_location(kobo) != span_location(place["source"], place["span"])


def test_kobo_report_that_moved_still_wins(sw, rows):
    kobo = _setup(sw, rows)
    moved = rows["kobo-moved"]

    sw.put(kobo, moved["source"], moved["span"], 12.0, after_now(5))

    percent, _progress = sw.pull()
    assert percent == pytest.approx(12, abs=0.01)
    assert sw.get(kobo)["CurrentBookmark"]["Location"] == span_location(
        moved["source"], moved["span"])


def test_unmoved_report_with_no_other_writer_is_unchanged(sw, rows):
    """The Kobo is the last writer: repeating its own place is harmless and
    still answered as before (its place stays shared)."""
    sw.koreader("koreader-1", "KOReader", checked=True)
    kobo = sw.kobo_device("Kobo", checked=True)
    sw.download(kobo)
    place = rows["kobo"]
    sw.put(kobo, place["source"], place["span"], 10.0, _now() - timedelta(hours=1))
    sw.put(kobo, place["source"], place["span"], 10.0, after_now(5))

    assert sw.pull()[0] == pytest.approx(10, abs=0.01)
    assert sw.get(kobo)["CurrentBookmark"]["Location"] == span_location(
        place["source"], place["span"])


def test_unmoved_report_keeps_a_newer_percentage_only_place(sw, rows):
    """KOReader read a file the server cannot map to the Kobo's KEPUB, so it
    shared a percentage only and the shared row kept the Kobo's old Location.
    The Kobo repeating that Location must not undo the newer percentage."""
    from tests.unit.share_place_span_world import OTHER_DIGEST
    sw.koreader("koreader-1", "KOReader", checked=True)
    kobo = sw.kobo_device("Kobo", checked=True)
    sw.download(kobo)
    place = rows["kobo"]
    sw.put(kobo, place["source"], place["span"], 10.0, _now() - timedelta(hours=1))
    sw.settle(kobo)
    sw.push(rows["koreader"]["xpointer"], 40, client_at=after_now(2), document=OTHER_DIGEST)
    assert sw.pull()[0] == pytest.approx(40, abs=0.01)

    sw.put(kobo, place["source"], place["span"], 10.0, after_now(5))

    assert sw.pull()[0] == pytest.approx(40, abs=0.01)
