# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2804: say why a Kobo download did not count as holding the current KEPUB.

A reporter's Kobo downloaded a re-added book normally, yet its download row
had no completed_at, so every later sync held the place back with one line:
"this Kobo has not finished downloading the library's current KEPUB". That
line covers four different failures (the transfer was never confirmed, the
KEPUB was replaced, the KEPUB was rewritten after the download, the book was
re-sent), and the download itself logged nothing when it set no proof. These
tests drive the real download route through gevent (the production server)
and the real Kobo GET /state, and read what the DEBUG log says.
"""

import logging
import os

import pytest
from flask import send_file

from tests.unit.share_place_span_world import location_of, sw  # noqa: F401  (fixture)
from tests.unit.test_share_place_kobo_download_proof import (  # noqa: F401  (fixtures)
    _bookmark, _download, gates_enforced, rows, stale_kobo,
)

pytestmark = pytest.mark.unit


def _messages(caplog, needle):
    return [r.getMessage() for r in caplog.records if needle in r.getMessage()]


def _held(caplog):
    found = _messages(caplog, "Kobo place for book")
    assert found, caplog.text
    return found[-1]


def _download_logs(caplog):
    return _messages(caplog, "Kobo download of book")


def test_a_kepub_written_after_the_download_names_both_times(caplog, stale_kobo):
    sw = stale_kobo
    with caplog.at_level(logging.DEBUG, logger="cps"):
        bookmark = _bookmark(sw)
    assert location_of(bookmark) == sw.own

    message = _held(caplog)
    assert "has not finished downloading the library's current KEPUB" in message
    assert "the library KEPUB was written at " in message
    assert "after this Kobo's download at " in message


def test_a_range_download_logs_why_it_proves_nothing_and_the_held_place_says_so(
        monkeypatch, caplog, stale_kobo):
    sw = stale_kobo
    with caplog.at_level(logging.DEBUG, logger="cps"):
        served = _download(monkeypatch, sw, headers={"Range": "bytes=0-0"})
        assert served.status == 206
        bookmark = _bookmark(sw)
    assert location_of(bookmark) == sw.own

    logged = _download_logs(caplog)
    assert any("completion not recorded" in m and "Range" in m for m in logged), logged
    assert "no completed transfer was recorded" in _held(caplog)


def test_a_short_stream_logs_how_many_bytes_arrived(monkeypatch, caplog, stale_kobo):
    sw = stale_kobo
    from flask import Response

    def serve(*_a, **_k):
        def body():
            yield b"only part"
        return Response(body(), status=200, headers={"Content-Length": "100"})

    with caplog.at_level(logging.DEBUG, logger="cps"):
        _download(monkeypatch, sw, serve=serve)
    logged = _download_logs(caplog)
    assert any("completion not recorded" in m and "9 of 100 bytes" in m
               for m in logged), logged


def test_a_kepub_rewritten_during_the_download_logs_that(monkeypatch, caplog, stale_kobo):
    sw = stale_kobo

    def serve(*_a, **_k):
        # The library KEPUB is rewritten (here: touched) between the moment
        # the download was recorded and the end of the transfer.
        stat = os.stat(sw.kepub)
        os.utime(sw.kepub, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        return send_file(sw.kepub, conditional=True)

    with caplog.at_level(logging.DEBUG, logger="cps"):
        served = _download(monkeypatch, sw, serve=serve)
    assert served.status == 200 and served.complete
    logged = _download_logs(caplog)
    assert any("completion not recorded" in m
               and "library KEPUB changed during the download" in m
               for m in logged), logged


def test_a_full_download_logs_that_its_completion_was_recorded(
        monkeypatch, caplog, stale_kobo):
    sw = stale_kobo
    with caplog.at_level(logging.DEBUG, logger="cps"):
        served = _download(monkeypatch, sw)
        assert served.status == 200 and served.complete
        bookmark = _bookmark(sw)
    assert location_of(bookmark) == sw.exact

    logged = _download_logs(caplog)
    assert any("completion recorded" in m for m in logged), logged
    assert not any("completion not recorded" in m for m in logged), logged
