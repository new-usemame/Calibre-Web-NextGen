# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""#2289 — reading in the web reader never reached Hardcover.

Kobo and KOReader progress pushed to Hardcover; the web reader's position
reached the Kobo and KOReader carriers (#324, #1366) but stopped there, so a
user reading in the browser saw nothing move on Hardcover.

The web reader saves on every page turn (the SPA debounces to 800 ms), so the
push is queued on the worker instead of blocking the save, and coalesced: while
a push for a book is waiting, later saves update what it will send rather than
queueing more.

Second face: our devices finish a book at 99% (``FINISHED_PERCENT_THRESHOLD``)
but the Hardcover client only treated exactly 100 as finished, so a book
finished at 99.x% stayed "Currently Reading" there.
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
from cps.services.hardcover import HardcoverClient, STATUS_READ, STATUS_READING

pytestmark = pytest.mark.unit


@pytest.fixture
def web_reader(monkeypatch):
    """The real ``record_web_reader_progress`` on an in-memory app DB with
    Hardcover sync on, the reader's own token set and the worker recorded."""
    from cps import helper
    from cps.services import reading_position
    from cps.tasks import hardcover_sync

    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ub, "session", session)
    import cps.progress_syncing.protocols.kosync  # noqa: F401
    kosync = sys.modules["cps.progress_syncing.protocols.kosync"]
    monkeypatch.setattr(kosync.config, "config_read_column", 0, raising=False)
    monkeypatch.setattr(helper.config, "hardcover_sync_enabled", lambda: True,
                        raising=False)
    monkeypatch.setattr(hardcover_sync, "_pending_progress", {})

    queued = []
    monkeypatch.setattr(hardcover_sync.WorkerThread, "add",
                        staticmethod(lambda owner, task, **k: queued.append((owner, task, k))))

    pushed = []
    client = MagicMock()
    client.update_reading_progress.side_effect = lambda ids, pct: pushed.append((ids, pct))
    monkeypatch.setattr(hardcover_sync.hardcover, "HardcoverClient",
                        lambda token: (client.tokens.append(token), client)[1])
    client.tokens = []
    monkeypatch.setattr(hardcover_sync.TaskHardcoverReadingProgress, "_identifiers",
                        lambda self: {"hardcover-id": "9"})

    user = SimpleNamespace(id=7, name="reader", hardcover_token="tok-7")

    def save(percentage, **kwargs):
        advanced = reading_position.record_web_reader_progress(
            user, 42, percentage, **kwargs)
        session.commit()
        return advanced

    def run_queued():
        tasks = [task for _, task, _ in queued]
        queued.clear()
        for task in tasks:
            task.run(None)

    return SimpleNamespace(save=save, run_queued=run_queued, queued=queued,
                           pushed=pushed, client=client, user=user,
                           session=session, monkeypatch=monkeypatch, helper=helper)


def test_web_reader_progress_reaches_hardcover(web_reader):
    assert web_reader.save(55.0) is True

    assert [(owner, k.get("hidden")) for owner, _, k in web_reader.queued] == [("reader", True)]
    web_reader.run_queued()
    assert web_reader.pushed == [({"hardcover-id": "9"}, 55.0)]
    assert web_reader.client.tokens == ["tok-7"]


def test_page_turns_while_a_push_waits_send_only_the_latest_place(web_reader):
    for percentage in (40.0, 41.5, 43.0):
        web_reader.save(percentage)

    assert len(web_reader.queued) == 1
    web_reader.run_queued()
    assert web_reader.pushed == [({"hardcover-id": "9"}, 43.0)]

    # Once that push has run, the next page turn queues a fresh one.
    web_reader.save(47.0)
    web_reader.run_queued()
    assert [pct for _, pct in web_reader.pushed] == [43.0, 47.0]


def test_a_position_behind_another_device_is_not_sent(web_reader):
    web_reader.save(80.0)
    web_reader.run_queued()

    assert web_reader.save(10.0) is False

    assert web_reader.queued == []
    assert [pct for _, pct in web_reader.pushed] == [80.0]


def test_continuing_from_a_device_preview_is_not_sent(web_reader):
    web_reader.save(30.0, share_with_devices=False)

    assert web_reader.queued == []


def test_nothing_is_sent_when_hardcover_sync_is_off(web_reader):
    web_reader.monkeypatch.setattr(web_reader.helper.config, "hardcover_sync_enabled",
                                   lambda: False, raising=False)

    assert web_reader.save(30.0) is True
    assert web_reader.queued == []


def test_nothing_is_sent_without_the_readers_own_token(web_reader):
    web_reader.user.hardcover_token = None

    assert web_reader.save(30.0) is True
    assert web_reader.queued == []


def test_a_book_blacklisted_for_progress_is_not_sent(web_reader):
    web_reader.session.add(ub.HardcoverBookBlacklist(book_id=42,
                                                     blacklist_reading_progress=True))
    web_reader.session.commit()

    assert web_reader.save(30.0) is True
    assert web_reader.queued == []


def test_a_failed_enqueue_does_not_silence_the_book_for_good(web_reader):
    from cps.tasks import hardcover_sync

    def refuse(*a, **k):
        raise RuntimeError("worker unavailable")
    with web_reader.monkeypatch.context() as m:
        m.setattr(hardcover_sync.WorkerThread, "add", staticmethod(refuse))
        # The bookmark save itself still succeeds.
        assert web_reader.save(30.0) is True

    web_reader.save(35.0)
    web_reader.run_queued()
    assert [pct for _, pct in web_reader.pushed] == [35.0]


def test_a_hardcover_failure_does_not_block_the_next_push(web_reader):
    web_reader.client.update_reading_progress.side_effect = RuntimeError("HTTP 500")
    web_reader.save(30.0)
    web_reader.run_queued()

    web_reader.client.update_reading_progress.side_effect = (
        lambda ids, pct: web_reader.pushed.append((ids, pct)))
    web_reader.save(35.0)
    web_reader.run_queued()
    assert [pct for _, pct in web_reader.pushed] == [35.0]


# ── finished at the threshold our devices use ────────────────────────────────

def _client(user_book):
    client = HardcoverClient.__new__(HardcoverClient)
    client.get_user_book = MagicMock(return_value=user_book)
    client.add_book = MagicMock()
    client.change_book_status = MagicMock(
        side_effect=lambda book, status: {**book, "status_id": status})
    client.execute = MagicMock(return_value={"update_user_book_read": {"id": 8}})
    return client


def test_a_book_finished_at_99_percent_is_read_on_hardcover():
    """Locally 99.4% is FINISHED; Hardcover must not leave it Reading."""
    user_book = {"id": 12, "status_id": STATUS_READING, "edition": None,
                 "user_book_reads": [{"id": 34}]}
    client = _client(user_book)

    client.update_reading_progress({"hardcover-id": "9"}, 99.4)

    client.change_book_status.assert_called_once_with(user_book, STATUS_READ)


def test_finishing_at_99_percent_with_an_edition_records_the_finish():
    user_book = {"id": 12, "status_id": STATUS_READING,
                 "edition": {"id": 77, "pages": 400},
                 "user_book_reads": [{"id": 34, "started_at": "2026-01-02"}]}
    client = _client(user_book)

    client.update_reading_progress({"hardcover-id": "9"}, 99.2)

    variables = client.execute.call_args.kwargs["variables"]
    assert variables["pages"] == 400
    assert variables["finishedAt"] is not None


def test_just_under_the_threshold_is_still_reading():
    user_book = {"id": 12, "status_id": STATUS_READING, "edition": None,
                 "user_book_reads": [{"id": 34}]}
    client = _client(user_book)

    client.update_reading_progress({"hardcover-id": "9"}, 98.9)

    client.change_book_status.assert_not_called()
