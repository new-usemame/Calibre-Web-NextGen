# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Startup KEPUB backfill is an idempotent scan, not a surrogate-id watermark.

SQLite may reuse ``INTEGER PRIMARY KEY`` values after deletes, and this table is
regularly pruned during shelf/device reconciliation.  Startup therefore queues
the cheap scan whenever KEPUB preference is enabled; the task itself skips
books that already have KEPUB and converts only missing work.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.tasks import kepub_backfill


@pytest.fixture
def app_session():
    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _sync(session, user_id, book_id):
    row = ub.KoboSyncedBooks(user_id=user_id, book_id=book_id)
    session.add(row)
    session.commit()
    return row


@pytest.fixture(autouse=True)
def _enqueueable(monkeypatch):
    monkeypatch.setattr(kepub_backfill.config, "config_kobo_prefer_kepub", True, raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_use_google_drive", False, raising=False)
    queued = []
    monkeypatch.setattr(kepub_backfill, "enqueue_kepub_backfill",
                        lambda **kw: queued.append(kw) or True)
    return queued


def _assert_startup_scan_queued(queue):
    assert kepub_backfill.enqueue_startup_kepub_backfill() is True
    assert queue == [{"hidden": True}]


def test_startup_always_queues_the_idempotent_scan(_enqueueable):
    _assert_startup_scan_queued(_enqueueable)


def test_a_newly_paired_device_still_rearms_a_completed_backfill(
    app_session, monkeypatch, _enqueueable,
):
    for book_id in (1, 2, 3):
        _sync(app_session, user_id=1, book_id=book_id)
    monkeypatch.setattr(
        kepub_backfill.config, "config_kobo_kepub_backfill_completed", True, raising=False)

    for book_id in (1, 2, 3):
        _sync(app_session, user_id=2, book_id=book_id)

    _assert_startup_scan_queued(_enqueueable)


def test_delete_max_then_insert_cannot_suppress_the_startup_scan(app_session, _enqueueable):
    rows = [_sync(app_session, 1, book_id) for book_id in (1, 2, 3)]
    old_max = rows[-1].id
    app_session.delete(rows[-1])
    app_session.commit()
    replacement = _sync(app_session, 2, 4)
    assert replacement.id == old_max, "precondition: SQLite reused the deleted maximum id"

    _assert_startup_scan_queued(_enqueueable)


def test_clear_and_repopulate_with_lower_ids_cannot_suppress_the_startup_scan(
    app_session, _enqueueable,
):
    rows = [_sync(app_session, 1, book_id) for book_id in (1, 2, 3)]
    old_max = rows[-1].id
    app_session.query(ub.KoboSyncedBooks).delete()
    app_session.commit()
    replacement = _sync(app_session, 2, 9)
    assert replacement.id < old_max, "precondition: repopulation restarted below old watermark"

    _assert_startup_scan_queued(_enqueueable)


def test_id_reuse_is_irrelevant_even_when_legacy_watermark_equals_current_max(
    app_session, monkeypatch, _enqueueable,
):
    row = _sync(app_session, 1, 1)
    monkeypatch.setattr(
        kepub_backfill.config, "config_kobo_kepub_backfill_watermark", row.id, raising=False)
    app_session.delete(row)
    app_session.commit()
    replacement = _sync(app_session, 2, 2)
    assert replacement.id == row.id

    _assert_startup_scan_queued(_enqueueable)


@pytest.mark.parametrize(
    "prefer_kepub,kepubify,gdrive",
    [(False, "/bin/kepubify", False), (True, "", False), (True, "/bin/kepubify", True)],
)
def test_existing_startup_safety_gates_still_apply(
    monkeypatch, _enqueueable, prefer_kepub, kepubify, gdrive,
):
    monkeypatch.setattr(kepub_backfill.config, "config_kobo_prefer_kepub", prefer_kepub)
    monkeypatch.setattr(kepub_backfill.config, "config_kepubifypath", kepubify)
    monkeypatch.setattr(kepub_backfill.config, "config_use_google_drive", gdrive)

    # The startup path delegates these gates to the ordinary enqueue function.
    # Exercise the real function rather than the recording fixture.
    monkeypatch.undo()
    monkeypatch.setattr(kepub_backfill.config, "config_kobo_prefer_kepub", prefer_kepub,
                        raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_kepubifypath", kepubify, raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_use_google_drive", gdrive, raising=False)
    assert kepub_backfill.enqueue_startup_kepub_backfill() is False
