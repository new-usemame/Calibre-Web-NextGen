# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance tests for "Share my place" PR 3b: a checked Kobo receives a
checked KOReader's place as the exact KEPUB span.

Spec: state/kindle-kids/specs/2026-10-11-share-my-place.md, section 11 ("3b:
exact span"), section 5 (the decision) and section 9 (the Clara probe: with
the book closed, a server-moved Location is honoured exactly, forward and
backward, with no prompt).

* A checked, identified KOReader's XPointer becomes the library KEPUB span
  ``kepub_alignment.xpointer_to_span`` gives for it; a checked Kobo holding
  the current KEPUB gets that span on GET /state and on library/sync,
  backward moves included.
* Any other Kobo gets the percentage only: a KEPUB downloaded before the
  library KEPUB was last written, no download, a download in another format.
  When alignment fails, every Kobo gets the percentage only, and the Kobo's
  earlier span is not left behind to mislead a later one.
* Unchanged: an unchecked Kobo never receives it; a Kobo's own span report
  works as today; with the rollout switch off nothing changes (the merge
  bar); an unidentified KOReader keeps today's percentage-only bridge.

Real book (``alice-pg11`` and its served KEPUB, the ``kepub_alignment``
fixtures), real kosync HTTP, real Kobo handlers (``share_place_span_world``).
"""

import os
import shutil
import zipfile
from datetime import timedelta

import pytest

from cps.services import kepub_alignment
from tests.unit.share_place_span_world import (  # noqa: F401  (fixture)
    BOOK, OTHER_DIGEST, _now, after_now, location_of, span_location, span_rows, sw,
)

pytestmark = pytest.mark.unit

SWITCH = "CWNG_SHARE_PLACE_GATES"


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    monkeypatch.setenv(SWITCH, "1")


@pytest.fixture
def rows():
    found = span_rows()
    return {"kobo-own": found[5], "early": found[100], "late": found[300],
            "kobo-later": found[200]}


@pytest.fixture
def account(sw, rows):
    """A checked KOReader, and a checked Kobo that holds the current KEPUB
    and last reported its own span (5%) an hour ago, fully synced."""
    sw.koreader_device = sw.koreader("koreader-1", "KOReader", checked=True)
    sw.checked = sw.kobo_device("Kobo Checked", checked=True)
    sw.download(sw.checked)
    own = rows["kobo-own"]
    sw.put(sw.checked, own["source"], own["span"], 5, _now() - timedelta(hours=1))
    sw.settle(sw.checked)
    return sw


def _expected(sw, row):
    """What the alignment gives for the row's XPointer on these very files."""
    found = kepub_alignment.xpointer_to_span(sw.epub, sw.kepub, row["xpointer"])
    assert found == (row["source"], row["span"])  # the fixture's ground truth
    return span_location(*found)


def _bookmark(sw, device):
    return sw.get(device).get("CurrentBookmark") or {}


# == The exact span reaches a checked Kobo holding the current KEPUB ==========

def test_a_checked_koreader_place_reaches_a_checked_kobo_as_the_exact_span(account, rows):
    sw = account
    sw.push(rows["late"]["xpointer"], 40)

    bookmark = _bookmark(sw, sw.checked)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == _expected(sw, rows["late"])
    assert sw.synced_location(sw.checked) == _expected(sw, rows["late"])


def test_a_backward_move_from_koreader_reaches_the_kobo_as_the_earlier_span(account, rows):
    sw = account
    sw.push(rows["late"]["xpointer"], 40, client_at=after_now(2))
    sw.settle(sw.checked)

    sw.push(rows["early"]["xpointer"], 20, client_at=after_now(4))

    bookmark = _bookmark(sw, sw.checked)
    assert bookmark.get("ProgressPercent") == pytest.approx(20)
    assert location_of(bookmark) == _expected(sw, rows["early"])
    assert sw.synced_location(sw.checked) == _expected(sw, rows["early"])


# == Percentage only for a Kobo that does not hold the current KEPUB ==========

@pytest.mark.parametrize("held", ["stale-kepub", "no-download", "epub-format"])
def test_a_checked_kobo_without_the_current_kepub_gets_the_percentage_only(
        account, rows, held):
    sw = account
    if held == "stale-kepub":
        sw.download(sw.checked, at=sw.kepub_written() - timedelta(days=1))
    elif held == "no-download":
        from cps import ub
        sw.session.query(ub.KoboDeviceBookDownload).delete()
        sw.session.commit()
    else:
        sw.download(sw.checked, book_format="epub")

    sw.push(rows["late"]["xpointer"], 40)

    bookmark = _bookmark(sw, sw.checked)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) is None
    assert sw.synced_location(sw.checked) is None


def test_the_span_is_sent_once_the_kobo_downloads_the_current_kepub(account, rows):
    sw = account
    sw.download(sw.checked, at=sw.kepub_written() - timedelta(days=1))
    sw.push(rows["late"]["xpointer"], 40)
    assert location_of(_bookmark(sw, sw.checked)) is None

    sw.download(sw.checked)

    assert location_of(_bookmark(sw, sw.checked)) == _expected(sw, rows["late"])


# == Alignment failure: the percentage only, and nothing misleading kept ======

def _kepub_of_another_edition(sw):
    """The library KEPUB re-made from a different text: one word changed in
    its last chapter, so it is no longer this EPUB's."""
    edited, changed = sw.kepub + ".edited", 0
    with zipfile.ZipFile(sw.kepub) as original, zipfile.ZipFile(edited, "w") as copy:
        for info in original.infolist():
            data = original.read(info)
            if "-h-12.htm" in info.filename and not changed:
                head, body = data.decode("utf-8").split("<body", 1)
                if "Alice" in body:
                    data = (head + "<body" + body.replace("Alice", "Alise", 1)).encode("utf-8")
                    changed += 1
            copy.writestr(info, data)
    assert changed == 1
    shutil.move(edited, sw.kepub)


@pytest.mark.parametrize("failure", ["xpointer-outside-the-book", "kepub-not-this-epub",
                                     "koreader-holds-another-file"])
def test_when_alignment_fails_every_kobo_gets_the_percentage_only(account, rows, failure):
    sw = account
    xpointer, document = rows["late"]["xpointer"], None
    if failure == "xpointer-outside-the-book":
        xpointer = "/body/DocFragment[400]/body/p[1]/text().0"
    elif failure == "kepub-not-this-epub":
        _kepub_of_another_edition(sw)
        assert kepub_alignment.xpointer_to_span(sw.epub, sw.kepub, xpointer) is None
    else:
        document = OTHER_DIGEST
    sw.download(sw.checked)  # holds the library KEPUB as it now is
    later = sw.kobo_device("Kobo Later", checked=True)
    sw.download(later)

    sw.push(xpointer, 40, document=document)

    for device in (sw.checked, later):
        bookmark = _bookmark(sw, device)
        assert bookmark.get("ProgressPercent") == pytest.approx(40), device.display_name
        assert location_of(bookmark) is None, device.display_name
    assert sw.synced_location(sw.checked) is None


# == Ownership and unchanged paths (controls) =================================

def test_an_unchecked_kobo_never_receives_the_span(account, rows):
    sw = account
    unchecked = sw.kobo_device("Kobo Unchecked", checked=False)
    sw.download(unchecked)
    own = rows["kobo-own"]
    sw.put(unchecked, own["source"], own["span"], 5, _now() - timedelta(hours=1))

    sw.push(rows["late"]["xpointer"], 40)

    assert location_of(_bookmark(sw, unchecked)) == span_location(own["source"], own["span"])


def test_a_kobos_own_later_span_report_wins_as_today(account, rows):
    sw = account
    sw.push(rows["late"]["xpointer"], 40, client_at=after_now(2))
    later = rows["kobo-later"]

    sw.put(sw.checked, later["source"], later["span"], 30, after_now(4))

    bookmark = _bookmark(sw, sw.checked)
    assert bookmark.get("ProgressPercent") == pytest.approx(30)
    assert location_of(bookmark) == span_location(later["source"], later["span"])


@pytest.mark.parametrize("path", ["switch-off", "unidentified-koreader"])
def test_unchanged_paths_keep_the_kobos_own_span_with_the_new_percentage(
        account, rows, monkeypatch, path):
    """Today the KOReader bridge moves only the percentage, forward only."""
    sw = account
    if path == "switch-off":
        monkeypatch.delenv(SWITCH, raising=False)
    own = rows["kobo-own"]

    sw.push(rows["late"]["xpointer"], 40, identified=path != "unidentified-koreader",
            raw_id="koreader-1" if path == "switch-off" else "stock-koreader",
            name="KOReader" if path == "switch-off" else "Stock KOReader")

    bookmark = _bookmark(sw, sw.checked)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == span_location(own["source"], own["span"])
