# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""A Kobo's place reaches KOReader and CrossPoint with CrossPoint's percentage.

A Kobo reports a whole ``ProgressPercent``, measured on its own KEPUB.
CrossPoint reports a fraction, measured on the EPUB it holds: each spine
document weighs its uncompressed size in the archive, and the reader's page
places it inside the document (``Epub::calculateProgress``). So the two
readers disagreed, and the furthest-wins compare saw only whole numbers.

When the reporting Kobo has the opt-in device setting ``align_percent`` on
and holds the library KEPUB as it is now, the shared ``kosync_progress`` row
gets CrossPoint's percentage at the Kobo's span. The Kobo's own bookmark keeps
the device's value, and anything short of that proof keeps the device's value
on the row too. With the setting off, the default, every reader, Hardcover and
the book page get the device's value, as before. Only a Kobo has the setting.

Real book (``alice-pg11`` and its served KEPUB), real kosync HTTP, real Kobo
handlers (``share_place_span_world``).
"""

import inspect
import os
import zipfile
from bisect import bisect_right
from datetime import timedelta
from itertools import accumulate

import pytest

from cps.services import kepub_alignment as ka
from tests.unit.share_place_span_world import (  # noqa: F401  (fixture)
    after_now, crosspoint_percent, span_rows, sw,
)
from tests.unit.test_kepub_alignment import EPUB, KEPUB, _book, _rows

pytestmark = pytest.mark.unit

SWITCH = "CWNG_SHARE_PLACE_GATES"
DEVICE_PERCENT = 56  # what the Kobo reports: a whole number


# == The percentage =========================================================

def _three_chapters(tmp_path):
    # The middle chapter's EPUB file is large for its text: CrossPoint weighs
    # bytes, not text, so the comment moves every place after it.
    padding = "<!--" + "x" * 5000 + "-->"
    epub = _book(tmp_path / "b.epub", [
        "<p>Short.</p>", f"<p>Alpha beta.</p>{padding}<p>Gamma delta.</p>", "<p>End.</p>"])
    kepub = _book(tmp_path / "b.kepub.epub", [
        '<p><span class="koboSpan" id="kobo.1.1">Short.</span></p>',
        '<p><span class="koboSpan" id="kobo.1.1">Alpha beta.</span></p>'
        '<p><span class="koboSpan" id="kobo.2.1">Gamma delta.</span></p>',
        '<p><span class="koboSpan" id="kobo.1.1">End.</span></p>'], spans=True)
    with zipfile.ZipFile(epub) as archive:
        sizes = [archive.getinfo(f"OEBPS/c{i}.xhtml").file_size for i in range(3)]
    return epub, kepub, sizes


def test_each_chapter_weighs_its_uncompressed_epub_bytes(tmp_path):
    epub, kepub, (s0, s1, s2) = _three_chapters(tmp_path)
    total = s0 + s1 + s2
    assert s1 > 5000 > s0 + s2

    # "Alphabeta." is 10 of the 21 non-whitespace characters of chapter 2.
    assert ka.span_book_percent(epub, kepub, "OEBPS/c1.xhtml", "kobo.2.1") == pytest.approx(
        100 * (s0 + 10 / 21 * s1) / total, abs=0.01)
    assert ka.span_book_percent(epub, kepub, "OEBPS/c0.xhtml", "kobo.1.1") == 0
    assert ka.span_book_percent(epub, kepub, "OEBPS/c2.xhtml", "kobo.1.1") == pytest.approx(
        100 * (s0 + s1) / total, abs=0.01)


def test_a_chapter_title_image_span_has_the_percentage_of_the_chapter_start(tmp_path):
    """Wind and Truth opens each chapter with a title image in ``kobo.1.1``.
    The Kobo reports that span on the first page of a chapter. It holds no
    text, so it is placed where the text after it starts."""
    image = '<img src="t.jpg" alt="Chapter two"/>'
    epub = _book(tmp_path / "b.epub", [
        "<p>Short.</p>", f"<h2>{image}</h2><p>Alpha beta.</p>", "<p>End.</p>"])
    kepub = _book(tmp_path / "b.kepub.epub", [
        '<p><span class="koboSpan" id="kobo.1.1">Short.</span></p>',
        f'<h2><span class="koboSpan" id="kobo.1.1">{image}</span></h2>'
        '<p><span class="koboSpan" id="kobo.2.1">Alpha beta.</span></p>',
        '<p><span class="koboSpan" id="kobo.1.1">End.</span></p>'], spans=True)
    start = ka.span_book_percent(epub, kepub, "OEBPS/c1.xhtml", "kobo.2.1")
    assert start is not None
    assert ka.span_book_percent(epub, kepub, "OEBPS/c1.xhtml", "kobo.1.1") == start


def test_a_span_that_does_not_resolve_has_no_percentage(tmp_path):
    epub, kepub, _sizes = _three_chapters(tmp_path)
    other = _book(tmp_path / "other.epub", ["<p>Short.</p>", "<p>Changed.</p>", "<p>End.</p>"])
    assert ka.span_book_percent(epub, kepub, "OEBPS/c1.xhtml", "kobo.9.9") is None
    assert ka.span_book_percent(epub, kepub, "OEBPS/missing.xhtml", "kobo.1.1") is None
    assert ka.span_book_percent(other, kepub, "OEBPS/c1.xhtml", "kobo.2.1") is None
    assert ka.span_book_percent(epub, tmp_path / "missing.kepub.epub",
                                "OEBPS/c1.xhtml", "kobo.2.1") is None


def test_crosspoint_maps_the_percentage_back_into_the_same_chapter():
    """CrossPoint's reverse map when it has no XPointer (``toCrossPoint``):
    bytes = book size x percentage, then the first spine item whose
    cumulative size reaches them."""
    members, _texts, offsets = ka._aligned_epub(EPUB, ka._kepub(KEPUB))
    sizes = ka._member_sizes(EPUB)
    cumulative = list(accumulate(sizes[member] for member in members))
    checked = 0
    for row in _rows():
        _spine, index = ka._span_start(EPUB, KEPUB, row["source"], row["span"])
        k = bisect_right(offsets, index) - 1
        if index == offsets[k]:
            continue  # a chapter's first character is CrossPoint's boundary
        percent = ka.span_book_percent(EPUB, KEPUB, row["source"], row["span"])
        target = int(cumulative[-1] * percent / 100)
        found = next(i for i, total in enumerate(cumulative) if total >= target)
        assert members[found] == members[k], row
        checked += 1
    assert checked > 400


# == What KOReader and CrossPoint pull ======================================

def _patch(world, device, body):
    """The device settings PATCH, as the Devices page sends it."""
    path = f"/api/annotations/devices/{device.public_id}"
    app = world.app
    with app.test_request_context(path, method="PATCH", json=body):
        endpoint, args = app.url_map.bind("localhost").match(path, method="PATCH")
        result = inspect.unwrap(app.view_functions[endpoint])(**args)
        response = result[0] if isinstance(result, tuple) else result
        status = result[1] if isinstance(result, tuple) else result.status_code
        data = response.get_json()
    world.session.expire_all()
    return status, data


def _align(world, device, on):
    status, data = _patch(world, device, {"align_percent": on})
    assert status == 200
    assert data["align_percent"] is on


def _kobo(request, sw, monkeypatch, align):
    checked = request.param == "checked"
    monkeypatch.setenv(SWITCH, "1" if checked else "0")
    sw.koreader("koreader-1", "KOReader", checked=checked)
    device = sw.kobo_device("Kobo", checked=checked)
    sw.download(device)
    if align:
        _align(sw, device, True)
    return device


@pytest.fixture(params=["checked", "unchecked"])
def kobo(request, sw, monkeypatch):
    """A Kobo that holds the library KEPUB, with the setting on: a checked
    one with the place gates on (latest-wins), or one under the gates off
    (furthest-wins)."""
    return _kobo(request, sw, monkeypatch, True)


@pytest.fixture(params=["checked", "unchecked"])
def kobo_off(request, sw, monkeypatch):
    """The same Kobo with the setting off."""
    return _kobo(request, sw, monkeypatch, False)


def test_the_setting_is_off_by_default_and_only_for_a_kobo(sw):
    from cps import ub
    column = ub.Device.__table__.c.align_percent
    assert column.default.arg is False
    assert str(column.server_default.arg) == "0"

    kobo = sw.kobo_device("Kobo", checked=False)
    status, data = _patch(sw, kobo, {"label": "Libra"})
    assert status == 200 and data["align_percent"] is False

    koreader = sw.koreader("koreader-1", "KOReader", checked=False)
    assert _patch(sw, koreader, {"align_percent": True}) == (
        400, {"error": "invalid_align_percent"})
    assert _patch(sw, kobo, {"align_percent": "yes"}) == (
        400, {"error": "invalid_align_percent"})
    assert sw.session.get(ub.Device, koreader.id).align_percent is False
    assert sw.session.get(ub.Device, kobo.id).align_percent is False


def test_a_kobos_span_reaches_koreader_as_crosspoints_percentage_and_exact_place(sw, kobo):
    row = span_rows()[300]

    sw.put(kobo, row["source"], row["span"], DEVICE_PERCENT, after_now())

    percent, progress = sw.pull()
    assert percent == crosspoint_percent(sw, row)
    assert percent != pytest.approx(DEVICE_PERCENT)
    assert progress == row["xpointer"]  # the exact place still crosses
    # The Kobo itself keeps the value it reported, which it echoes back.
    assert sw.get(kobo)["CurrentBookmark"]["ProgressPercent"] == pytest.approx(DEVICE_PERCENT)


def test_a_move_inside_one_whole_percent_still_moves_koreader(sw, kobo):
    rows = span_rows()
    sw.put(kobo, rows[300]["source"], rows[300]["span"], DEVICE_PERCENT, after_now(2))
    sw.put(kobo, rows[301]["source"], rows[301]["span"], DEVICE_PERCENT, after_now(4))

    percent, progress = sw.pull()
    assert percent == crosspoint_percent(sw, rows[301])
    assert progress == rows[301]["xpointer"]


def test_a_span_the_kepub_does_not_hold_shares_the_devices_percentage(sw, kobo):
    row = span_rows()[300]

    sw.put(kobo, row["source"], "kobo.9999.1", DEVICE_PERCENT, after_now())

    assert sw.pull()[0] == pytest.approx(DEVICE_PERCENT)


def test_a_kobo_holding_an_older_kepub_shares_the_devices_percentage(sw, kobo):
    row = span_rows()[300]
    sw.download(kobo, at=sw.kepub_written() - timedelta(days=1))

    sw.put(kobo, row["source"], row["span"], DEVICE_PERCENT, after_now())

    assert sw.pull()[0] == pytest.approx(DEVICE_PERCENT)


def test_a_failure_to_compute_it_does_not_fail_the_kobos_sync(sw, kobo, monkeypatch):
    from cps.services import koreader_position

    def broken(**_kwargs):
        raise RuntimeError("cannot read the book")

    monkeypatch.setattr(koreader_position, "kobo_share_percent", broken)
    row = span_rows()[300]

    sw.put(kobo, row["source"], row["span"], DEVICE_PERCENT, after_now())  # 200, Success

    assert sw.pull()[0] == pytest.approx(DEVICE_PERCENT)


# == What Hardcover and the book page show ==================================

@pytest.fixture
def hardcover_pushes(monkeypatch):
    from cps import kobo as kobo_module
    pushes = []
    monkeypatch.setattr(kobo_module, "push_reading_state_to_hardcover",
                        lambda _user, book, percent: pushes.append((book.id, percent)))
    return pushes


def test_hardcover_gets_the_percentage_koreader_gets(sw, kobo, hardcover_pushes):
    row = span_rows()[300]

    sw.put(kobo, row["source"], row["span"], DEVICE_PERCENT, after_now())

    assert hardcover_pushes == [(sw.book.id, crosspoint_percent(sw, row))]


def test_hardcover_gets_the_devices_percentage_without_the_proof(sw, kobo, hardcover_pushes):
    row = span_rows()[300]

    sw.put(kobo, row["source"], "kobo.9999.1", DEVICE_PERCENT, after_now())

    assert hardcover_pushes == [(sw.book.id, pytest.approx(DEVICE_PERCENT))]


def _shown(world):
    from cps import helper
    return helper.get_kosync_progress_display(world.session, world.reader.id, world.book.id)[0]


def test_the_book_page_shows_the_percentage_of_the_same_kobo_report(sw, kobo):
    row = span_rows()[300]

    sw.put(kobo, row["source"], row["span"], DEVICE_PERCENT, after_now())

    if os.environ[SWITCH] == "1":
        assert _shown(sw) == crosspoint_percent(sw, row)
    else:
        # With the gates off the row gets its own receipt, so it cannot be
        # matched to the bookmark. The page keeps the device's value.
        assert _shown(sw) == pytest.approx(DEVICE_PERCENT)


def test_the_book_page_shows_the_bookmark_after_a_later_koreader_push(sw, kobo):
    from cps import ub
    rows = span_rows()
    sw.put(kobo, rows[300]["source"], rows[300]["span"], DEVICE_PERCENT, after_now(2))
    sw.push(rows[310]["xpointer"], 61.25, client_at=after_now(4))

    bookmark = sw.session.query(ub.KoboReadingState).filter_by(
        user_id=sw.reader.id, book_id=sw.book.id).one().current_bookmark
    assert _shown(sw) == bookmark.progress_percent
    assert _shown(sw) != crosspoint_percent(sw, rows[300])


# == The setting off: the device's whole number, as before ==================

def test_with_the_setting_off_koreader_gets_the_devices_percentage(sw, kobo_off):
    row = span_rows()[300]

    sw.put(kobo_off, row["source"], row["span"], DEVICE_PERCENT, after_now())

    percent, progress = sw.pull()
    assert percent == pytest.approx(DEVICE_PERCENT)
    assert progress == row["xpointer"]  # the exact place still crosses


def test_with_the_setting_off_hardcover_gets_the_devices_percentage(
        sw, kobo_off, hardcover_pushes):
    row = span_rows()[300]

    sw.put(kobo_off, row["source"], row["span"], DEVICE_PERCENT, after_now())

    assert hardcover_pushes == [(sw.book.id, pytest.approx(DEVICE_PERCENT))]


def test_with_the_setting_off_the_book_page_shows_the_bookmark(sw, kobo_off):
    row = span_rows()[300]

    sw.put(kobo_off, row["source"], row["span"], DEVICE_PERCENT, after_now())

    assert _shown(sw) == pytest.approx(DEVICE_PERCENT)


def test_turning_the_setting_off_shares_the_devices_percentage_again(sw, kobo):
    rows = span_rows()
    sw.put(kobo, rows[300]["source"], rows[300]["span"], DEVICE_PERCENT, after_now(2))
    assert sw.pull()[0] == crosspoint_percent(sw, rows[300])

    _align(sw, kobo, False)
    sw.put(kobo, rows[350]["source"], rows[350]["span"], 90, after_now(4))

    assert sw.pull()[0] == pytest.approx(90)
    assert _shown(sw) == pytest.approx(90)
