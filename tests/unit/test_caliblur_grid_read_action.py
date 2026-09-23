# -*- coding: utf-8 -*-
# Calibre-Web Automated - fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2249: caliBlur's grid read action must only open formats the reader renders.

`handleDirectReading()` in caliBlur.js kept its own format list, which offered
html/mobi/azw3/fb2 and fell back to a book's first format. read_book() renders
none of those, so a MOBI-only book's read icon opened a tab that read_book()
answers with a 404 (upstream CWA redirects to the library with "Selected book is
unavailable" instead), while the same book's detail page correctly showed no
Read button.

The fix keeps one list, on the server: check_read_formats() orders the reader's
formats by preference, the detail page opens reader_list[0], and the grid reads
the same list through the `reader_formats` filter (a data attribute).

The JS tests execute the SHIPPED handleDirectReading() in Node against a stub
link and window, so they are about what the click does, not about source text.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from cps import jinjia
from cps.helper import check_read_formats

pytestmark = pytest.mark.unit

CALIBLUR_JS = Path(__file__).resolve().parents[2] / "cps" / "static" / "js" / "caliBlur.js"
NODE = shutil.which("node")


def _book(*formats):
    return SimpleNamespace(data=[SimpleNamespace(format=f) for f in formats])


# --- the server list -------------------------------------------------------

@pytest.mark.parametrize("formats", [("MOBI",), ("AZW3",), ("MOBI", "AZW3", "FB2", "HTML"), ()])
def test_a_book_with_no_renderable_format_has_nothing_to_read(formats):
    assert check_read_formats(_book(*formats)) == []


def test_the_preferred_format_comes_first_whatever_order_the_library_lists_them():
    # A PDF added before its EPUB must not make "Read now" open the PDF.
    assert check_read_formats(_book("PDF", "MOBI", "EPUB")) == ["epub", "pdf"]
    assert check_read_formats(_book("CBZ", "TXT")) == ["txt", "cbz"]


def _viewer(can_read):
    return mock.patch.object(jinjia, "current_user",
                             SimpleNamespace(role_viewer=lambda: can_read))


def test_filter_renders_the_reader_list_for_a_viewer():
    with _viewer(True):
        assert jinjia.reader_formats_filter(_book("MOBI", "PDF", "EPUB")) == "epub,pdf"
        assert jinjia.reader_formats_filter(_book("MOBI")) == ""


def test_filter_offers_the_audio_player_after_any_reading_format():
    # read_book() plays these through listenmp3.html; the old JS reached it by
    # falling back to the first format, so an audiobook's read icon must still work.
    with _viewer(True):
        assert jinjia.reader_formats_filter(_book("M4B")) == "m4b"
        assert jinjia.reader_formats_filter(_book("MP3", "EPUB", "MOBI")) == "epub,mp3"


def test_filter_offers_nothing_to_a_user_who_may_not_read():
    # read_book() is @viewer_required; the detail page hides "Read now" for them too.
    with _viewer(False):
        assert jinjia.reader_formats_filter(_book("EPUB")) == ""


# --- the click -------------------------------------------------------------

HARNESS = r"""
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const scenario = JSON.parse(process.argv[3]);
const start = src.indexOf('function handleDirectReading(');
const end = src.indexOf('function handleReadStatusToggle(', start);
if (start < 0 || end < 0) throw new Error('handleDirectReading not found');
const attrs = scenario.attrs;
const $link = {
  // jQuery's .data() coerces "5" to 5; mirror that so both code shapes run.
  data: (k) => { const v = attrs['data-' + k]; return v !== undefined && /^\d+$/.test(v) ? Number(v) : v; },
  attr: (k) => attrs[k],
};
const opened = [];
const window = { scriptRoot: scenario.scriptRoot || '', open: (url) => opened.push(url),
                 location: { href: 'about:grid' } };
// Executes the shipped function verbatim; the input is our own repo file.
const handleDirectReading = new Function('window', 'return (' + src.slice(start, end).trim() + ');')(window);
const handled = handleDirectReading($link);
process.stdout.write(JSON.stringify({ handled, opened, location: window.location.href }) + "\n");
"""


def _click(tmp_path, attrs, script_root=""):
    harness = tmp_path / "harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    out = subprocess.run(
        [NODE, str(harness), str(CALIBLUR_JS),
         json.dumps({"attrs": attrs, "scriptRoot": script_root})],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _link(book_id, formats, read_formats):
    return {"href": "/book/%s" % book_id, "data-book-id": str(book_id),
            "data-book-formats": formats, "data-book-read-formats": read_formats}


needs_node = pytest.mark.skipif(
    NODE is None, reason="node missing (2026-09-23, owner: CWNG test suite; CI images ship node)")


@needs_node
@pytest.mark.parametrize("formats", ["mobi", "azw3", "mobi,azw3,fb2,html"])
def test_read_on_an_unreadable_book_opens_no_reader_and_leaves_the_click_to_the_cover(tmp_path, formats):
    result = _click(tmp_path, _link(7, formats, ""))
    assert result["opened"] == [], "a reader was opened for %s" % formats
    # Not handled, so the caller does not preventDefault: the click does what the
    # rest of the cover does (details modal, or the detail page).
    assert result["handled"] is not True
    assert result["location"] == "about:grid"


@needs_node
def test_read_on_a_cover_with_no_book_does_nothing(tmp_path):
    # The global-library <span class="book-cover-link"> has no id, href or formats.
    result = _click(tmp_path, {})
    assert result["opened"] == [] and result["location"] == "about:grid"


@needs_node
def test_read_opens_the_first_format_the_server_offers(tmp_path):
    result = _click(tmp_path, _link(5, "pdf,mobi,epub", "epub,pdf"), script_root="/calibre")
    assert result["handled"] is True
    assert result["opened"] == ["/calibre/read/5/epub"]
