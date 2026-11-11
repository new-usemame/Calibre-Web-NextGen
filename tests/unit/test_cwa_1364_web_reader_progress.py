# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Acceptance tests for CWA #1364 — web reader not saving progress.

Reporter (CWA upstream, today): "The web reader is not saving progress
for any book. Whenever I open a book it initially opens in the last
(correct) position, which is apparently cached in the browser, but
after a couple of seconds it sends me back to the beginning of the
book. ... book_read_link fields are never updated."

Reproduced on our build (cwn-local v4.0.107): bug exists on our code
path. Two distinct root causes:

(1) **localStorage corruption on early locationchange events.** The
``epub-progress.js`` locationchange handler called ``calculateProgress()``
unconditionally and wrote its return value to localStorage. Before
``epub.locations.generate()`` resolves, ``calculateProgress()`` returns 0
— because there are no locations to map the current CFI against. The
handler then wrote ``localStorage[key] = 0``, wiping the valid prior
value. The qFinished/restore path read ``localStorage = 0``, called
``display(cfiFromPercentage(0))``, and the user landed at the
beginning of the book — even though they were reading at 35%.

(2) **``book_read_link`` fields never bumped from the web reader.**
The Kobo + KOSync paths bump ``last_time_started_reading`` and
``times_started_reading``; the web reader's ``read_book`` route never
did. A user who only reads through the browser had no read history
recorded — ``/read``, ``/unread`` filters never reflected web-reader
activity.

These tests source-pin both fixes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
JS_PATH = REPO_ROOT / "cps" / "static" / "js" / "reading" / "epub-progress.js"
WEB_PY = REPO_ROOT / "cps" / "web.py"


def _js_source() -> str:
    return JS_PATH.read_text()


def _web_source() -> str:
    return WEB_PY.read_text()


# --- Fix #1 source-pins -------------------------------------------------------


def test_locationchange_handler_guards_localstorage_save_on_locations_ready():
    """The localStorage.setItem in the locationchange handler must be
    gated on ``epub.locations._locations.length > 0``. Without the
    guard, an early locationchange (before locations.generate
    resolves) writes 0 to localStorage and corrupts the user's saved
    position."""
    src = _js_source()
    handler_match = re.search(
        r"window\.addEventListener\(['\"]locationchange['\"].*?\}\);",
        src,
        re.DOTALL,
    )
    assert handler_match, "Could not locate locationchange handler"
    body = handler_match.group(0)
    # The setItem call must be inside an `if (...)` that references
    # epub.locations._locations.length.
    assert "epub.locations._locations" in body, (
        "The locationchange handler must check `epub.locations._locations` "
        "before writing to localStorage — otherwise an early locationchange "
        "writes the (necessarily-zero) calculateProgress() result and wipes "
        "the user's prior position. See CWA #1364."
    )
    assert ".length" in body, (
        "Guard must check `epub.locations._locations.length` is non-zero "
        "before the localStorage save."
    )


def test_cwa_1364_anchor_comment_present_in_js():
    """Anchor comment so a future refactor doesn't unwittingly remove
    the locations-ready guard."""
    src = _js_source()
    assert "CWA #1364" in src, (
        "epub-progress.js must reference CWA #1364 so future code "
        "archaeology can find the rationale for the locations-ready "
        "guard around the localStorage save."
    )


# --- Fix #2 source-pins -------------------------------------------------------


# The upsert and its debounce (refresh is not a new session, a finished book
# stays finished) moved into ``cps.services.read_status.record_reader_open``,
# shared with the new UI reader (#2750). They are exercised as behaviour, not
# source text, in tests/unit/test_2750_new_reader_open_marks_reading.py, and
# the classic route's write in test_reader_lookup_and_stop_reading_2223.py.


def test_read_book_handles_db_failure_gracefully():
    """The book_read_link UPSERT is best-effort; failing to record the
    read event must not break page rendering. Pin the try/except."""
    src = _web_source()
    match = re.search(
        r"def read_book\(book_id, book_format\):.*?(?=\n(?:@web\.route|def \w))",
        src,
        re.DOTALL,
    )
    body = match.group(0)
    # The book_read_link block must be inside a try/except.
    assert "Failed to record web reader open in book_read_link" in body, (
        "read_book's book_read_link UPSERT must be wrapped in try/except "
        "so a DB error doesn't break page rendering. The log message lets "
        "future debugging connect a missing book_read_link update to the "
        "exception class."
    )


def test_cwa_1364_anchor_comment_present_in_web_py():
    """Anchor comment in web.py near the book_read_link logic."""
    src = _web_source()
    assert "CWA #1364" in src, (
        "cps/web.py must reference CWA #1364 near the book_read_link "
        "UPSERT so future code archaeology can find the rationale."
    )


def test_datetime_timezone_imported_in_web_py():
    """The book_read_link UPSERT uses `datetime.now(timezone.utc)` —
    confirm both are importable at module level so the route doesn't
    NameError on first call."""
    src = _web_source()
    assert re.search(
        r"^from datetime import (?:.*\b)?datetime",
        src,
        re.MULTILINE,
    ), "cps/web.py must `from datetime import datetime` at module level."
    assert re.search(
        r"^from datetime import (?:.*\b)?timezone",
        src,
        re.MULTILINE,
    ), "cps/web.py must `from datetime import timezone` at module level."
