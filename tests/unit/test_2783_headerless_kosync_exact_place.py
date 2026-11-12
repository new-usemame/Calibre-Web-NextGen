# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2783: a KOSync app that sends no ``X-CWNG-Device-ID`` (JustRead, stock
KOReader) moves the Kobo to its exact place, not only its percentage.

Reporter, v4.1.62 with DEBUG on: after a JustRead save the Kobo bookmark had
JustRead's 30.56% but still the Kobo's own old ``kobo.74.1``, so the book
showed the new percentage and opened at the old spot.

Such a requester is unidentified, and the operator's rule for it is the
shared place, "that's just how it's been behaving" (share-my-place spec,
section 8). It keeps the forward-only acceptance it has always had; what
changes is that an accepted report carries its XPointer as the library KEPUB
span, the same conversion a checked CWNG-plugin reader gets, so a Kobo
holding the current KEPUB opens where the app left off.

Real book, real kosync HTTP, real Kobo handlers (``share_place_span_world``).
"""

from datetime import timedelta

import pytest

from cps.services import kepub_alignment
from tests.unit.share_place_span_world import (  # noqa: F401  (fixture)
    OTHER_DIGEST, _now, after_now, location_of, span_location, span_rows, sw,
)

pytestmark = pytest.mark.unit

APP = dict(identified=False, raw_id="justread-ipad", name="JustRead")


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    monkeypatch.setenv("CWNG_SHARE_PLACE_GATES", "1")


@pytest.fixture
def rows():
    found = span_rows()
    return {"kobo-own": found[5], "early": found[100], "late": found[300]}


@pytest.fixture
def account(sw, rows):
    """A checked Kobo holding the current KEPUB that last reported its own
    span (5%) an hour ago, fully synced. No CWNG-plugin reader exists."""
    sw.checked = sw.kobo_device("Kobo Checked", checked=True)
    sw.download(sw.checked)
    own = rows["kobo-own"]
    sw.put(sw.checked, own["source"], own["span"], 5, _now() - timedelta(hours=1))
    sw.settle(sw.checked)
    return sw


def _expected(sw, row):
    found = kepub_alignment.xpointer_to_span(sw.epub, sw.kepub, row["xpointer"])
    assert found == (row["source"], row["span"])
    return span_location(*found)


def _bookmark(sw, device):
    return sw.get(device).get("CurrentBookmark") or {}


def test_a_headerless_app_save_moves_the_kobo_to_its_exact_place(account, rows):
    sw = account
    sw.push(rows["late"]["xpointer"], 40, **APP)

    bookmark = _bookmark(sw, sw.checked)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == _expected(sw, rows["late"])
    assert sw.synced_location(sw.checked) == _expected(sw, rows["late"])


def test_a_headerless_app_still_cannot_move_the_kobo_backward(account, rows):
    """Forward-only acceptance is unchanged: the app has no box to say a
    backward move is deliberate, so a lower report leaves the Kobo alone."""
    sw = account
    sw.push(rows["late"]["xpointer"], 40, client_at=after_now(2), **APP)
    sw.push(rows["early"]["xpointer"], 20, client_at=after_now(4), **APP)

    bookmark = _bookmark(sw, sw.checked)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == _expected(sw, rows["late"])


def test_a_headerless_place_that_does_not_convert_keeps_the_kobos_location(
        account, rows):
    """A save against another file of the book cannot be placed in this
    KEPUB: the Kobo keeps its own Location with the new percentage, never a
    bookmark with no Location (Nickel opens that at the cover)."""
    sw = account
    own = rows["kobo-own"]
    sw.push(rows["late"]["xpointer"], 40, document=OTHER_DIGEST, **APP)

    bookmark = _bookmark(sw, sw.checked)
    assert bookmark.get("ProgressPercent") == pytest.approx(40)
    assert location_of(bookmark) == span_location(own["source"], own["span"])


def test_an_unchecked_kobo_never_receives_a_headerless_apps_place(account, rows):
    sw = account
    unchecked = sw.kobo_device("Kobo Unchecked", checked=False)
    sw.download(unchecked)
    before = location_of(_bookmark(sw, unchecked))

    sw.push(rows["late"]["xpointer"], 40, **APP)

    assert location_of(_bookmark(sw, unchecked)) == before
