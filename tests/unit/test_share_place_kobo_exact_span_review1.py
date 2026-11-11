# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Acceptance tests for "Share my place" PR 3b review 1 (spec section 14c).

The rule: every path to an exact Location is proven; every other path is
inexact, and an inexact serve always arms the echo guard. A checked Kobo
receives an exact Location, its own held Location with the new percentage,
or no shared place at all; never a Location it cannot be shown to hold and
never a bookmark without a Location.

Starting points: Luna's review probes in
state/kindle-kids/legs/share-place-pr3b/review-probes/. These pin outcomes
(what each device GETs, what the shared place is afterwards), not the guard's
storage. Real book, real kosync HTTP, real Kobo handlers.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import pytest
from flask import g

from cps import kobo
from tests.unit.share_place_span_world import (  # noqa: F401  (fixture)
    _now, after_now, location_of, span_location, span_rows, sw,
)

pytestmark = pytest.mark.unit

SWITCH = "CWNG_SHARE_PLACE_GATES"
COVER = {"source": "OEBPS/wrap0000.html", "span": "kobo.1.1"}  # first spine item


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    monkeypatch.setenv(SWITCH, "1")


@pytest.fixture
def rows():
    found = span_rows()
    return {"held": found[5], "first-text": found[0], "own-later": found[56],
            "shared": found[300], "koreader-later": found[350]}


def _loc(row):
    return span_location(row["source"], row["span"])


def _put(sw, device, row, percent, seconds):
    if row is None:
        sw.put(device, None, None, percent, after_now(seconds))
    else:
        sw.put(device, row["source"], row["span"], percent, after_now(seconds))


@pytest.fixture
def world(sw, rows):
    """A checked KOReader; a checked Kobo ("target") holding the current
    KEPUB that last reported ``held`` (5%) an hour ago, fully synced; and a
    checked observer Kobo holding the current KEPUB."""
    sw.koreader_device = sw.koreader("koreader-1", "KOReader", checked=True)
    sw.target = sw.kobo_device("Kobo Target", checked=True)
    sw.download(sw.target)
    held = rows["held"]
    sw.put(sw.target, held["source"], held["span"], 5, _now() - timedelta(hours=1))
    sw.settle(sw.target)
    sw.observer = sw.kobo_device("Kobo Observer", checked=True)
    sw.download(sw.observer)
    return sw


def _not_rewound(sw, percent=40):
    """Neither the other checked Kobo nor KOReader moved off ``percent``."""
    assert sw.get(sw.observer)["CurrentBookmark"]["ProgressPercent"] == pytest.approx(percent)
    assert sw.pull()[0] == pytest.approx(percent)


# == 1. No library KEPUB is inexact, and guarded =============================

def test_without_a_library_kepub_the_kobo_gets_its_own_location_and_its_close_does_not_rewind(
        world, rows):
    sw = world
    _put(sw, sw.observer, rows["shared"], 40, 2)  # a Kobo-origin shared place
    os.unlink(sw.kepub)

    bookmark = sw.get(sw.target)["CurrentBookmark"]
    assert location_of(bookmark) == _loc(rows["held"])
    assert bookmark["ProgressPercent"] == pytest.approx(40)

    _put(sw, sw.target, rows["held"], 5, 4)  # Nickel closes where it opened
    _not_rewound(sw)


# == 2. Only a completed download proves the current KEPUB ===================

def test_an_armed_redownload_is_not_proof_until_the_file_is_downloaded(world, rows):
    from cps import ub
    from cps.services.kobo_post_download_restore import arm_pending_restore, record_download
    sw = world
    # The library KEPUB was replaced after the target's download; the next
    # sync re-sent the book (arming a restore) but the Kobo has not fetched it.
    replaced = datetime.now(timezone.utc).timestamp() - 2
    os.utime(sw.kepub, (replaced, replaced))
    row = sw.session.query(ub.KoboDeviceBookDownload).filter_by(
        device_id=sw.target.id, book_id=sw.book.id).one()
    row.downloaded_at = datetime.fromtimestamp(replaced - 10, timezone.utc).replace(tzinfo=None)
    row.restore_state = "served"
    sw.session.commit()
    arm_pending_restore(device_id=sw.target.id, book_ids=[sw.book.id],
                        log=logging.getLogger("test"))
    sw.session.commit()

    sw.push(rows["shared"]["xpointer"], 40)
    assert location_of(sw.get(sw.target)["CurrentBookmark"]) == _loc(rows["held"])

    record_download(device_id=sw.target.id, book_id=sw.book.id, book_format="kepub",
                    log=logging.getLogger("test"))
    sw.session.commit()
    sw.session.expire_all()

    assert location_of(sw.get(sw.target)["CurrentBookmark"]) == _loc(rows["shared"])


# == 3. A Kobo-origin span is exact only from a Kobo that held the current KEPUB

def test_a_span_from_a_kobo_holding_a_stale_kepub_is_not_exact_for_one_holding_the_current(
        world, rows):
    sw = world
    stale = sw.kobo_device("Kobo Stale", checked=True)
    sw.download(stale, at=sw.kepub_written() - timedelta(days=1))
    _put(sw, stale, rows["shared"], 40, 2)

    bookmark = sw.get(sw.target)["CurrentBookmark"]
    assert location_of(bookmark) == _loc(rows["held"])
    assert bookmark["ProgressPercent"] == pytest.approx(40)

    _put(sw, sw.target, rows["held"], 5, 4)
    _not_rewound(sw)


# == 4. A checked Kobo with no Location of its own gets no shared place ======

@pytest.fixture
def placeless(sw, rows):
    """A checked KOReader shared 40%; a checked Kobo that never reported the
    book holds a stale KEPUB (not eligible for exact delivery)."""
    sw.koreader_device = sw.koreader("koreader-1", "KOReader", checked=True)
    sw.observer = sw.kobo_device("Kobo Observer", checked=True)
    sw.download(sw.observer)
    sw.target = sw.kobo_device("Kobo New", checked=True)
    sw.download(sw.target, at=sw.kepub_written() - timedelta(days=1))
    sw.push(rows["shared"]["xpointer"], 40)
    return sw


def test_a_kobo_with_no_location_of_its_own_gets_no_shared_place(placeless):
    bookmark = placeless.get(placeless.target).get("CurrentBookmark") or {}
    assert location_of(bookmark) is None
    assert not bookmark.get("ProgressPercent")  # not the shared 40%


@pytest.mark.parametrize("opened_at", ["cover-span", "no-location", "first-text-span"])
def test_its_first_open_at_the_start_does_not_rewind(placeless, rows, opened_at):
    sw = placeless
    sw.get(sw.target)
    sw.sync(sw.target)
    target = {"cover-span": COVER, "no-location": None,
              "first-text-span": rows["first-text"]}[opened_at]

    _put(sw, sw.target, target, 0, 4)

    _not_rewound(sw)


def test_once_it_has_a_location_of_its_own_inexact_serves_resume(placeless, rows):
    sw = placeless
    sw.get(sw.target)
    _put(sw, sw.target, rows["own-later"], 10, 4)  # real reading: wins
    assert sw.pull()[0] == pytest.approx(10)

    sw.push(rows["koreader-later"]["xpointer"], 50, client_at=after_now(6))

    bookmark = sw.get(sw.target)["CurrentBookmark"]
    assert location_of(bookmark) == _loc(rows["own-later"])
    assert bookmark["ProgressPercent"] == pytest.approx(50)


# == 5. An unresolved Kobo never gets a converted span =======================

def test_an_unresolved_kobo_gets_the_stored_native_location_not_the_converted_span(
        world, rows):
    sw = world
    sw.push(rows["shared"]["xpointer"], 40)
    # The checked target holds the current KEPUB: it does get the exact span,
    # so the shared bookmark now carries a KOReader-converted Location.
    assert location_of(sw.get(sw.target)["CurrentBookmark"]) == _loc(rows["shared"])

    with sw.app.test_request_context(f"/v1/library/{sw.book.uuid}/state", method="GET"):
        g.annotation_origin_device_id = None
        response = kobo.HandleStateRequest.__wrapped__(sw.book.uuid)
    sw.session.expire_all()
    assert response.status_code == 200
    bookmark = response.get_json()[0]["CurrentBookmark"]
    assert location_of(bookmark) == _loc(rows["held"])
