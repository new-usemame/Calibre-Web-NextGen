# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""The KEPUB backfill must re-arm when its work-set grows.

`config_kobo_kepub_backfill_completed` was a one-shot global boolean, but the
task's work-set is `select distinct book_id from kobo_synced_books` — and
pairing a device is *precisely* what grows that set. So the latch answered
"completed" while the newly-synced books had no KEPUB at all.

Measured on a live instance immediately after a Kobo Clara BW paired: **216
books synced, 34 with a KEPUB, 182 without**, with the flag set to completed.
Those 182 convert at download time with the device waiting, and a conversion
failure delivers plain EPUB — which cannot reliably hold highlights on a Kobo
(upstream janeczku/calibre-web#1484). The stale latch therefore lands as
"highlighting doesn't work on my new Kobo".

A boolean cannot express "done for the library as it was THEN". A monotonic
high-water mark over KoboSyncedBooks.id can.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.tasks import kepub_backfill


@pytest.fixture
def app_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    monkeypatch.setattr(ub, "get_new_session_instance", lambda: session)
    monkeypatch.setattr(kepub_backfill.ub, "get_new_session_instance", lambda: session)
    yield session
    session.close()


def _sync(session, user_id, book_id):
    row = ub.KoboSyncedBooks(user_id=user_id, book_id=book_id)
    session.add(row)
    session.commit()
    return row


@pytest.fixture(autouse=True)
def _enqueueable(monkeypatch):
    """Make enqueue reachable; the gate under test is the watermark, not these."""
    monkeypatch.setattr(kepub_backfill.config, "config_kobo_prefer_kepub", True, raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_use_google_drive", False, raising=False)
    queued = []
    monkeypatch.setattr(kepub_backfill, "enqueue_kepub_backfill",
                        lambda **kw: queued.append(kw) or True)
    return queued


def test_a_newly_paired_device_rearms_a_completed_backfill(app_session, monkeypatch, _enqueueable):
    for book_id in (1, 2, 3):
        _sync(app_session, user_id=1, book_id=book_id)
    covered = kepub_backfill._synced_books_watermark(app_session)
    monkeypatch.setattr(kepub_backfill.config, "config_kobo_kepub_backfill_completed", True, raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_kobo_kepub_backfill_watermark", covered, raising=False)

    assert kepub_backfill.enqueue_startup_kepub_backfill() is False, "nothing new yet"

    # a second device pairs and syncs the same library
    for book_id in (1, 2, 3):
        _sync(app_session, user_id=2, book_id=book_id)

    assert kepub_backfill.enqueue_startup_kepub_backfill() is True
    assert _enqueueable == [{"hidden": True}]


def test_no_new_sync_rows_does_not_rearm(app_session, monkeypatch, _enqueueable):
    _sync(app_session, user_id=1, book_id=1)
    covered = kepub_backfill._synced_books_watermark(app_session)
    monkeypatch.setattr(kepub_backfill.config, "config_kobo_kepub_backfill_watermark", covered, raising=False)

    assert kepub_backfill.enqueue_startup_kepub_backfill() is False
    assert _enqueueable == []


def test_an_upgraded_install_performs_exactly_one_catch_up_scan(app_session, monkeypatch, _enqueueable):
    """completed=True with no watermark is the population carrying unconverted
    books. One catch-up scan is the point, not a regression -- and it is cheap,
    because _backfill_one_book already skips any book that has a KEPUB."""
    for book_id in (1, 2):
        _sync(app_session, user_id=1, book_id=book_id)
    monkeypatch.setattr(kepub_backfill.config, "config_kobo_kepub_backfill_completed", True, raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_kobo_kepub_backfill_watermark", 0, raising=False)

    assert kepub_backfill.enqueue_startup_kepub_backfill() is True

    # once that run records what it covered, it stops
    monkeypatch.setattr(kepub_backfill.config, "config_kobo_kepub_backfill_watermark",
                        kepub_backfill._synced_books_watermark(app_session), raising=False)
    assert kepub_backfill.enqueue_startup_kepub_backfill() is False


def test_the_boolean_alone_no_longer_gates_startup(app_session, monkeypatch, _enqueueable):
    """The regression in one line: completed=True must NOT suppress new work."""
    _sync(app_session, user_id=1, book_id=1)
    monkeypatch.setattr(kepub_backfill.config, "config_kobo_kepub_backfill_completed", True, raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_kobo_kepub_backfill_watermark", 0, raising=False)

    assert kepub_backfill.enqueue_startup_kepub_backfill() is True


def test_watermark_is_none_when_nothing_is_outstanding(app_session, monkeypatch):
    _sync(app_session, user_id=1, book_id=1)
    monkeypatch.setattr(kepub_backfill.config, "config_kobo_kepub_backfill_watermark",
                        kepub_backfill._synced_books_watermark(app_session), raising=False)
    assert kepub_backfill.outstanding_kepub_backfill_watermark() is None


def test_an_empty_sync_table_is_not_outstanding(app_session, monkeypatch):
    monkeypatch.setattr(kepub_backfill.config, "config_kobo_kepub_backfill_watermark", 0, raising=False)
    assert kepub_backfill.outstanding_kepub_backfill_watermark() is None
    assert kepub_backfill.enqueue_startup_kepub_backfill() is False
