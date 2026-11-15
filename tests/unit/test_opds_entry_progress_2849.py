# Calibre-Web-NextGen
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""#2849: an OPDS entry carries the reader's shared place as ``<cwng:progress>``.

A catalog app showing "55% read" in a list had to ask the sync server once per
book. Each entry now carries the same shared place an unidentified KOSync GET
serves for that book (a 0-1 fraction, furthest across every checksum of the
book), and nothing a device kept to itself: a device with "share my place" off
writes only its own position, and a Kobo's own bookmark is not a shared place.
These tests play the app through the real blueprint and ``feed.xml``.
"""

from datetime import datetime, timezone
import xml.etree.ElementTree as ET

import pytest

from cps import db, ub
from cps.progress_syncing.models import KOSyncProgress
from tests.unit.test_opds_entry_read_status_2849 import (
    CWNG, FEEDS, OTHER_ID, READER_ID, _book_id_from_acquisition, _entries, _world)

pytestmark = pytest.mark.unit

CHECKSUM_3 = "c" * 32
CHECKSUM_1 = "d" * 32
# book id -> the fraction the entry must carry (absent: no element at all)
EXPECTED = {1: 0.8, 2: 0.55, 3: 0.425}


def _place(session, user_id, document, percentage):
    now = datetime.now(timezone.utc)
    session.add(KOSyncProgress(
        user_id=user_id, document=document, progress="/body/DocFragment[4]",
        percentage=percentage, device="koreader", device_id="dev",
        timestamp=now))


def _places(session):
    session.add(db.BookFormatChecksum(3, "EPUB", CHECKSUM_3))
    session.add(db.BookFormatChecksum(1, "EPUB", CHECKSUM_1))
    session.commit()
    # Book 2: the canonical shared row.
    _place(session, READER_ID, "2", 55.0)
    # Book 3: stored only under a file checksum, as an old KOReader push leaves it.
    _place(session, READER_ID, CHECKSUM_3, 42.5)
    # Book 1: two keys for one book; the furthest is the shared place served.
    _place(session, READER_ID, "1", 30.0)
    _place(session, READER_ID, CHECKSUM_1, 80.0)
    # Book 4: places this reader's devices kept to themselves.
    session.add(ub.DeviceReadingPosition(device_id=1, book_id=4, progress_percent=70.0))
    state = ub.KoboReadingState(user_id=READER_ID, book_id=4)
    session.add(state)
    session.flush()
    session.add(ub.KoboBookmark(kobo_reading_state_id=state.id, progress_percent=70.0))
    # Book 5: another account's place.
    _place(session, OTHER_ID, "5", 90.0)
    session.commit()


@pytest.fixture
def signed_in(monkeypatch):
    client, session, engine = _world(monkeypatch, anonymous=False)
    _places(session)
    try:
        yield client
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def guest(monkeypatch):
    client, session, engine = _world(monkeypatch, anonymous=True)
    _places(session)
    try:
        yield client
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize("url", FEEDS)
def test_each_entry_carries_this_readers_shared_place_and_nothing_private(signed_in, url):
    seen = {}
    for entry in _entries(signed_in, url):
        places = entry.findall(CWNG + "progress")
        assert len(places) <= 1, ET.tostring(entry)[:400]
        if places:
            seen[_book_id_from_acquisition(entry)] = float(places[0].text)
    assert seen == pytest.approx(EXPECTED)


@pytest.mark.parametrize("url", FEEDS)
def test_a_guest_gets_no_place(guest, url):
    for entry in _entries(guest, url):
        assert entry.find(CWNG + "progress") is None
