# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2688: a Kobo that finishes downloading a book is re-sent the shared place.

Nickel treats every (re-)download as "forget what I had for this book", so it
opens at the cover. A shared place the Kobo was sent *before* that download is
gone from the device, yet the device's reading-state cursor is already past
it, so nothing re-sends it until the account's place changes again.

Once a download is proven complete (the same proof that makes the span exact),
the next sync re-sends the shared place once, if the box is on and the shared
place beats the device's own report. These tests drive the real download route
through gevent, then ask the real library/sync what that Kobo is sent.
"""

import json

import pytest

from cps import ub
from tests.unit.share_place_span_world import (  # noqa: F401  (fixture)
    after_now, span_location, span_rows, sw,
)
from tests.unit.test_share_place_kobo_download_proof import _download

pytestmark = pytest.mark.unit

SWITCH = "CWNG_SHARE_PLACE_GATES"


@pytest.fixture(autouse=True)
def gates_enforced(monkeypatch):
    monkeypatch.setenv(SWITCH, "1")


@pytest.fixture
def kobo(sw):
    """A checked Kobo that holds no copy of the book yet."""
    sw.koreader_device = sw.koreader("koreader-1", "KOReader", checked=True)
    sw.checked = sw.kobo_device("Kobo Checked", checked=True)
    return sw


@pytest.fixture
def placed(kobo):
    """A checked KOReader shared a place at 40% and the Kobo then synced, so its
    cursor is past that place before it downloads the book."""
    sw = kobo
    late = span_rows()[300]
    sw.push(late["xpointer"], 40)
    sw.settle(sw.checked)
    sw.exact = span_location(late["source"], late["span"])
    return sw


def _sent_for_book(sw, response):
    return str(sw.book.uuid) in json.dumps(response.get_json())


def _nudges(sw):
    return sw.session.query(ub.KoboSharePlaceNudge).filter_by(
        device_id=sw.checked.id, book_id=sw.book.id).count()


def test_a_proven_download_re_sends_the_exact_shared_place(monkeypatch, placed):
    sw = placed
    served = _download(monkeypatch, sw)
    assert served.status == 200 and served.complete

    assert sw.synced_location(sw.checked) == sw.exact


def test_the_re_sent_place_is_sent_only_once(monkeypatch, placed):
    sw = placed
    _download(monkeypatch, sw)
    assert sw.synced_location(sw.checked) == sw.exact

    # The next sync acknowledges the page that carried it; nothing follows.
    for _ in range(2):
        assert not _sent_for_book(sw, sw.sync(sw.checked))
    assert _nudges(sw) == 0


def test_a_kobo_whose_box_is_off_is_sent_nothing(monkeypatch, placed):
    sw = placed
    sw.share(sw.checked, False)
    _download(monkeypatch, sw)

    assert _nudges(sw) == 0
    assert not _sent_for_book(sw, sw.sync(sw.checked))


def test_a_download_with_no_shared_place_arms_nothing(monkeypatch, kobo):
    sw = kobo
    sw.settle(sw.checked)
    _download(monkeypatch, sw)

    assert _nudges(sw) == 0
    assert not _sent_for_book(sw, sw.sync(sw.checked))


def test_a_download_that_fails_partway_arms_nothing(monkeypatch, placed):
    sw = placed
    served = _download(monkeypatch, sw, broken=True)
    assert not served.complete

    assert _nudges(sw) == 0
    assert not _sent_for_book(sw, sw.sync(sw.checked))


def test_a_kobo_whose_own_place_is_newer_keeps_it(monkeypatch, placed):
    """The Kobo read on by itself, with its box off, after the shared place was
    written; the box was turned on later. Its own report beats the shared place
    at the arbiter, so no shared-place nudge is armed. What the download sends
    is the Kobo's own place, restored by the existing own-place restore."""
    sw = placed
    own = span_rows()[5]
    sw.share(sw.checked, False)
    sw.put(sw.checked, own["source"], own["span"], 5, after_now(60))
    sw.settle(sw.checked)
    sw.share(sw.checked, True)
    assert _nudges(sw) == 0  # the turn-on already decided: the Kobo's own place wins

    _download(monkeypatch, sw)

    assert _nudges(sw) == 0
    assert sw.synced_location(sw.checked) == span_location(own["source"], own["span"])
