# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance tests for the "Share my place" PR 3b echo guard.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 14
(Clara probe 2) and section 14a (the final guard). A checked Kobo served a
shared place it cannot take exactly keeps its own Location with the new
percentage; Nickel opens the book there (or at the cover) and, on closing,
PUTs that old place back with a fresh clock. Under latest-wins that echo
would rewind every checked device. So:

* serving an inexact place arms a guard with the Location the Kobo held;
* while armed, a PUT at exactly that Location, at the cover (no Location,
  or the first spine item at 0%), or at the page-start span of the armed
  page is an echo: acknowledged, but it neither moves the shared place nor
  changes the shared or account status;
* anything else is real reading and wins: forward by a page, or back by
  more than a page;
* an exact serve, turning the box off, the first real move and 7 days each
  drop the guard.

Unchecked Kobos and the switch off are unchanged.

Real book, real kosync HTTP, real Kobo handlers (``share_place_span_world``).
"""

from datetime import timedelta

import pytest
from sqlalchemy import DateTime, select, update

from cps import ub
from cps.progress_syncing.models import KOSyncProgress
from tests.unit.share_place_span_world import (  # noqa: F401  (fixture)
    BOOK, _now, after_now, location_of, span_location, span_rows, sw,
)

pytestmark = pytest.mark.unit

SWITCH = "CWNG_SHARE_PLACE_GATES"
COVER = ("OEBPS/wrap0000.html", "kobo.1.1")  # the KEPUB's first spine item


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    monkeypatch.setenv(SWITCH, "1")


@pytest.fixture
def rows():
    """Spans of one chapter file, in reading order, plus an earlier chapter
    and KOReader's place much later in the book."""
    found = span_rows()
    return {"earlier-chapter": found[44], "page-back-far": found[48],
            "page-start": found[55], "held": found[56], "page-forward": found[57],
            "koreader": found[300]}


HELD_PERCENT = 10.0


def _held(rows):
    return span_location(rows["held"]["source"], rows["held"]["span"])


def _arm(sw, rows, *, kobo_name="Kobo Checked", checked=True, koreader_percent=40):
    """A checked KOReader, and a Kobo that reported ``held`` (10%) an hour
    ago and holds a KEPUB older than the library's. KOReader then shares its
    place; the Kobo's next sync serves it inexactly: ``held`` with the new
    percentage."""
    sw.koreader_device = sw.koreader("koreader-1", "KOReader", checked=True)
    # Another checked Kobo, holding the current KEPUB, watches the shared place.
    sw.observer = sw.kobo_device("Kobo Observer", checked=True)
    sw.download(sw.observer)
    kobo = sw.kobo_device(kobo_name, checked=checked)
    sw.download(kobo, at=sw.kepub_written() - timedelta(days=1))
    held = rows["held"]
    sw.put(kobo, held["source"], held["span"], HELD_PERCENT, _now() - timedelta(hours=1))
    sw.settle(kobo)
    sw.push(rows["koreader"]["xpointer"], koreader_percent, client_at=after_now(2))
    return kobo


def _served_inexactly(sw, rows, kobo, percent=40):
    """The Kobo syncs and is served the shared percentage on its own held
    Location (section 14: never a bookmark with no Location)."""
    assert sw.synced_location(kobo) == _held(rows)
    assert sw.get(kobo)["CurrentBookmark"]["ProgressPercent"] == pytest.approx(percent)


def _close_at(sw, kobo, row_or_none, percent, *, status="Reading", seconds=4):
    if row_or_none is None:
        sw.put(kobo, None, None, percent, after_now(seconds), status=status)
    else:
        sw.put(kobo, row_or_none["source"], row_or_none["span"], percent,
               after_now(seconds), status=status)


def _not_rewound(sw, rows, percent=40):
    """The shared place is still KOReader's: the other checked Kobo and
    KOReader itself both get it."""
    assert sw.get(sw.observer)["CurrentBookmark"]["ProgressPercent"] == pytest.approx(percent)
    seen_percent, progress = sw.pull()
    assert seen_percent == pytest.approx(percent)
    assert progress == rows["koreader"]["xpointer"]


def _won(sw, percent):
    """The Kobo's report is the shared place: the other checked Kobo gets it,
    and so does KOReader's KOSync GET (section 14b: mirrored latest-wins)."""
    assert sw.get(sw.observer)["CurrentBookmark"]["ProgressPercent"] == pytest.approx(percent)
    assert sw.pull()[0] == pytest.approx(percent)


@pytest.fixture
def armed(sw, rows):
    kobo = _arm(sw, rows)
    _served_inexactly(sw, rows, kobo)
    sw.kobo = kobo
    return sw


# == Echoes while armed: acknowledged, never a rewind ========================

def test_closing_at_the_exact_old_location_does_not_rewind_the_shared_place(armed, rows):
    sw = armed
    _close_at(sw, sw.kobo, rows["held"], HELD_PERCENT)  # acknowledged (asserted in put)
    _not_rewound(sw, rows)


def test_closing_at_the_page_start_span_before_it_does_not_rewind(armed, rows):
    sw = armed
    # OBSERVED on the Clara: held kobo.44.10, echoed kobo.41.1 (same page).
    _close_at(sw, sw.kobo, rows["page-start"], HELD_PERCENT - 0.4)
    _not_rewound(sw, rows)


@pytest.mark.parametrize("cover", ["first-spine-item", "no-location"])
def test_the_cover_at_zero_does_not_rewind_or_change_a_finished_status(sw, rows, cover):
    kobo = _arm(sw, rows)
    # The other checked Kobo finishes the book at KOReader's place.
    koreader = rows["koreader"]
    _close_at(sw, sw.observer, koreader, 100, status="Finished", seconds=3)
    assert sw.read_row(sw.reader, BOOK).read_status == ub.ReadBook.STATUS_FINISHED
    _served_inexactly(sw, rows, kobo, percent=100)

    if cover == "no-location":
        _close_at(sw, kobo, None, 0, seconds=5)
    else:
        _close_at(sw, kobo, {"source": COVER[0], "span": COVER[1]}, 0, seconds=5)

    observed = sw.get(sw.observer)
    assert observed["CurrentBookmark"]["ProgressPercent"] == pytest.approx(100)
    assert observed["StatusInfo"]["Status"] == "Finished"
    assert sw.read_row(sw.reader, BOOK).read_status == ub.ReadBook.STATUS_FINISHED


# == Real reading while armed: wins, and clears the guard ====================

def test_reading_one_page_forward_after_an_inexact_serve_wins(armed, rows):
    sw = armed
    _close_at(sw, sw.kobo, rows["page-forward"], HELD_PERCENT + 0.4)
    _won(sw, HELD_PERCENT + 0.4)


@pytest.mark.parametrize("back", ["same-file-more-than-a-page", "earlier-chapter"])
def test_a_deliberate_flip_back_of_more_than_a_page_wins_and_clears_the_guard(
        armed, rows, back):
    sw = armed
    target = rows["page-back-far" if back == "same-file-more-than-a-page" else "earlier-chapter"]
    _close_at(sw, sw.kobo, target, HELD_PERCENT - 3)
    _won(sw, HELD_PERCENT - 3)

    # The guard is gone: a return to the held place is real reading too.
    _close_at(sw, sw.kobo, rows["held"], HELD_PERCENT, seconds=6)
    _won(sw, HELD_PERCENT)


# == The guard's lifecycle ===================================================

def _age_every_stored_time(sw, days):
    """Move every stored timestamp ``days`` into the past, as if the guard
    had been armed that long ago (no sleeping; the guard's own storage is the
    implementation's choice, so every DateTime column of every table moves,
    written back through SQLAlchemy so it keeps its stored format)."""
    shift = timedelta(days=days)
    tables = {t for t in ub.Base.metadata.sorted_tables} | {KOSyncProgress.__table__}
    for table in tables:
        stamps = [c for c in table.columns if isinstance(c.type, DateTime)]
        keys = list(table.primary_key.columns)
        if not stamps or not keys:
            continue
        try:
            found = sw.session.execute(select(*keys, *stamps)).all()
        except Exception:  # a table this world does not create
            sw.session.rollback()
            continue
        for row in found:
            where = [key == row[i] for i, key in enumerate(keys)]
            values = {c.name: row[len(keys) + i] - shift
                      for i, c in enumerate(stamps) if row[len(keys) + i] is not None}
            if values:
                sw.session.execute(update(table).where(*where).values(**values))
    sw.session.commit()
    sw.session.expire_all()


def test_a_deliberate_return_to_the_old_place_after_seven_days_wins(armed, rows):
    sw = armed
    _age_every_stored_time(sw, 8)
    _close_at(sw, sw.kobo, rows["held"], HELD_PERCENT)
    _won(sw, HELD_PERCENT)


def test_turning_the_box_off_clears_the_guard(armed, rows):
    sw = armed
    sw.share(sw.kobo, False)
    sw.share(sw.kobo, True)

    _close_at(sw, sw.kobo, rows["held"], HELD_PERCENT)

    _won(sw, HELD_PERCENT)


def test_an_exact_serve_leaves_no_guard(armed, rows):
    sw = armed
    sw.download(sw.kobo)  # now holds the current KEPUB
    koreader = rows["koreader"]
    assert location_of(sw.get(sw.kobo)["CurrentBookmark"]) == span_location(
        koreader["source"], koreader["span"])

    _close_at(sw, sw.kobo, rows["held"], HELD_PERCENT)

    _won(sw, HELD_PERCENT)


# == Unchanged paths (controls) ==============================================

def test_an_unchecked_kobos_report_at_its_own_place_stays_its_own(sw, rows):
    kobo = _arm(sw, rows, kobo_name="Kobo Unchecked", checked=False)
    sw.sync(kobo)

    _close_at(sw, kobo, rows["held"], HELD_PERCENT)

    bookmark = sw.get(kobo)["CurrentBookmark"]
    assert location_of(bookmark) == _held(rows)
    assert bookmark["ProgressPercent"] == pytest.approx(HELD_PERCENT)
    _not_rewound(sw, rows)


def test_with_the_switch_off_a_report_at_the_old_place_behaves_as_on_main(
        sw, rows, monkeypatch):
    monkeypatch.setenv(SWITCH, "0")
    kobo = _arm(sw, rows)
    sw.sync(kobo)

    _close_at(sw, kobo, rows["held"], HELD_PERCENT)

    # Main: a Kobo's own report is its place (no guard).
    bookmark = sw.get(kobo)["CurrentBookmark"]
    assert bookmark["ProgressPercent"] == pytest.approx(HELD_PERCENT)
    assert location_of(bookmark) == _held(rows)


# == A checked Kobo's place on KOSync (section 14b) ==========================

def _kobo_after_koreader(sw, rows, *, checked, name="Kobo"):
    """A Kobo holding the current KEPUB reported ``held`` an hour ago; a
    checked KOReader has since shared its place at 40%."""
    sw.koreader_device = sw.koreader("koreader-1", "KOReader", checked=True)
    kobo = sw.kobo_device(name, checked=checked)
    sw.download(kobo)
    held = rows["held"]
    sw.put(kobo, held["source"], held["span"], HELD_PERCENT, _now() - timedelta(hours=1))
    sw.settle(kobo)
    sw.push(rows["koreader"]["xpointer"], 40, client_at=after_now(2))
    assert sw.pull()[0] == pytest.approx(40)
    return kobo


def test_koreader_gets_a_checked_kobos_backward_move(sw, rows):
    kobo = _kobo_after_koreader(sw, rows, checked=True)

    _close_at(sw, kobo, rows["page-back-far"], HELD_PERCENT - 3)

    assert sw.pull()[0] == pytest.approx(HELD_PERCENT - 3)


def test_an_unchecked_kobos_report_does_not_reach_koreader(sw, rows):
    kobo = _kobo_after_koreader(sw, rows, checked=False, name="Kobo Unchecked")

    _close_at(sw, kobo, rows["koreader"], 60)  # even ahead of KOReader

    seen_percent, progress = sw.pull()
    assert seen_percent == pytest.approx(40)
    assert progress == rows["koreader"]["xpointer"]


def test_with_the_switch_off_a_kobos_backward_move_leaves_koreader_where_it_was(
        sw, rows, monkeypatch):
    monkeypatch.setenv(SWITCH, "0")
    kobo = _kobo_after_koreader(sw, rows, checked=True)

    _close_at(sw, kobo, rows["page-back-far"], HELD_PERCENT - 3)

    assert sw.pull()[0] == pytest.approx(40)  # main: furthest wins
