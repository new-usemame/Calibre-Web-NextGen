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
// Runs caliBlur's shipped cover click handler, and the real handleDirectReading()
// it calls, against a stub link, event and window. The input is our own repo file.
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const scenario = JSON.parse(process.argv[3]);

function slice(from, to) {
  const a = src.indexOf(from), b = src.indexOf(to, a);
  if (a < 0 || b < 0) throw new Error('not found: ' + from);
  return src.slice(a, b).trim();
}
function handler() {
  const at = src.indexOf("on('click.directReading'");
  const a = src.indexOf('function', at);
  let depth = 0;
  for (let i = src.indexOf('{', a); i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}' && --depth === 0) return src.slice(a, i + 1);
  }
  throw new Error('click handler not found');
}

const attrs = scenario.attrs;
const W = 150, H = 225, LEFT = 100, TOP = 200;
const link = {};
const $link = {
  // jQuery's .data() coerces "5" to 5; mirror that so both code shapes run.
  data: (k) => { const v = attrs['data-' + k]; return v !== undefined && /^\d+$/.test(v) ? Number(v) : v; },
  attr: (k) => attrs[k],
  closest: () => $link, offset: () => ({ left: LEFT, top: TOP }),
  outerWidth: () => W, outerHeight: () => H,
};
const $ = (x) => (x === link ? $link : { width: () => 1366 });
const opened = [], actions = [];
const window = { scriptRoot: scenario.scriptRoot || '', open: (url) => opened.push(url),
                 location: { href: 'about:grid' } };
const other = (name) => () => actions.push(name);
const make = new Function('$', 'window', 'handleReadStatusToggle', 'handleEditMetadata',
  'handleSendToEReader',
  'const handleDirectReading = (' + slice('function handleDirectReading(', 'function handleReadStatusToggle(') + ');' +
  'return (' + handler() + ');');
const onClick = make($, window, other('toggle'), other('edit'), other('send'));

let prevented = false;
// A click on the centre of the cover, where the read icon is drawn.
onClick.call(link, { pageX: LEFT + W / 2, pageY: TOP + H / 2, preventDefault: () => { prevented = true; } });
process.stdout.write(JSON.stringify({ prevented, opened, actions, location: window.location.href }) + "\n");
"""


def _click_centre(tmp_path, attrs, script_root=""):
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
    result = _click_centre(tmp_path, _link(7, formats, ""))
    assert result["opened"] == [], "a reader was opened for %s" % formats
    # The click is not cancelled, so it follows the cover's link to the detail
    # page, like a click anywhere else on the cover. No other action fires.
    assert result["prevented"] is False
    assert result["actions"] == [] and result["location"] == "about:grid"


@needs_node
def test_read_on_a_cover_with_no_book_id_opens_nothing(tmp_path):
    # The global-library <span class="book-cover-link"> has no id or link.
    result = _click_centre(tmp_path, {"data-book-read-formats": "epub"})
    assert result["opened"] == [] and result["location"] == "about:grid"
    assert result["prevented"] is False


@needs_node
def test_read_opens_the_first_format_the_server_offers(tmp_path):
    result = _click_centre(tmp_path, _link(5, "pdf,mobi,epub", "epub,pdf"), script_root="/calibre")
    assert result["opened"] == ["/calibre/read/5/epub"]
    # Cancelled, so the grid page itself stays put while the reader opens in a tab.
    assert result["prevented"] is True
    assert result["actions"] == [] and result["location"] == "about:grid"
