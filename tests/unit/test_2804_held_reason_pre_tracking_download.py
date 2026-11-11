# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2804: the held-place reason names a download from before completion tracking.

A download row written before CWNG recorded finished downloads has no
``completed_at`` and no file identity. When its post-download annotation
request came back empty, ``restore_state`` is ``empty``, which the proof
deliberately refuses (it cannot tell a finished download from a book the
Kobo was told about and never fetched). The place is rightly held, but the
DEBUG reason fell through to "the library KEPUB was written at X, after this
Kobo's download at Y" even when X was before Y (OBSERVED on #2804, book 99:
written 01:36:44 UTC, downloaded 10:52:28 UTC the same day). A reader acting
on that would re-convert the book instead of downloading it again.
"""

import logging
from datetime import timedelta

import pytest

from tests.unit.share_place_span_world import location_of, sw  # noqa: F401  (fixture)
from tests.unit.test_share_place_kobo_download_proof import (  # noqa: F401  (fixtures)
    _bookmark, gates_enforced, rows, stale_kobo,
)
from cps import ub

pytestmark = pytest.mark.unit


def _held_message(sw, caplog):
    with caplog.at_level(logging.DEBUG, logger="cps.services.kobo_place_gate"):
        bookmark = _bookmark(sw)
    records = [r.getMessage() for r in caplog.records
               if "Kobo place for book" in r.getMessage()]
    assert records, caplog.text
    return bookmark, records[-1]


def _row(sw):
    return sw.session.query(ub.KoboDeviceBookDownload).filter_by(
        device_id=sw.checked.id, book_id=sw.book.id).one()


def test_a_pre_tracking_download_after_the_kepub_says_download_again(caplog, stale_kobo):
    sw = stale_kobo
    row = _row(sw)
    row.downloaded_at = sw.kepub_written() + timedelta(hours=9)
    row.restore_state = "empty"
    row.completed_at = None
    row.kepub_file_identity = None
    sw.session.commit()

    bookmark, message = _held_message(sw, caplog)

    assert location_of(bookmark) == sw.own  # still held: an empty row is no proof
    assert "held" in message
    assert "before CWNG recorded finished downloads" in message
    assert "download the book again" in message
    assert "after this Kobo's download" not in message


def test_a_tracked_download_older_than_the_kepub_still_says_the_kepub_is_newer(
        caplog, stale_kobo):
    sw = stale_kobo
    row = _row(sw)
    assert row.restore_state == "pending" and row.completed_at is None
    assert row.downloaded_at < sw.kepub_written()

    bookmark, message = _held_message(sw, caplog)

    assert location_of(bookmark) == sw.own
    assert "the library KEPUB was written at" in message
    assert "after this Kobo's download" in message
    assert "before CWNG recorded finished downloads" not in message
