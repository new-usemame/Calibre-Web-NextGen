# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""#2289 — marking a book read in CWNG never reached Hardcover.

The reporter had Hardcover sync set up and books carrying ``hardcover-id``,
marked them Read / Currently Reading in the web UI, and their Hardcover account
stayed empty. Only Kobo progress, KOReader progress and Kobo-synced shelf adds
talked to Hardcover; every manual read-status path (classic toggle, new UI,
bulk edit, KOReader library plugin) funnels through
``helper.edit_book_read_status`` and none of them did.

A second face of the same gap: a device finishing a book whose Hardcover entry
has no edition (``hardcover-id`` only, the reporter's exact shape) never marked
it Read, because the Read transition sat inside the page-count branch.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from cps import ub
import cps.progress_syncing.models  # noqa: F401 — registers KOSyncProgress on ub.Base
from cps.services.hardcover import (
    HardcoverClient, STATUS_READ, STATUS_READING,
)

pytestmark = pytest.mark.unit

WTR = 1


def _client(user_book):
    """The service without its network-owning constructor."""
    client = HardcoverClient.__new__(HardcoverClient)
    client.get_user_book = MagicMock(return_value=user_book)
    client.add_book = MagicMock(return_value={"id": 99, "status_id": STATUS_READ})
    client.change_book_status = MagicMock(
        side_effect=lambda book, status: {**book, "status_id": status})
    client.execute = MagicMock(return_value={})
    return client


# ── the finished-without-an-edition face ─────────────────────────────────────

def test_finishing_a_book_with_no_edition_marks_it_read():
    """hardcover-id only -> the user_book has "edition": null. A Kobo or
    KOReader reaching 100% must still move it to Read."""
    user_book = {"id": 12, "status_id": STATUS_READING, "edition": None,
                 "user_book_reads": [{"id": 34}]}
    client = _client(user_book)

    client.update_reading_progress({"hardcover-id": "123"}, 100)

    client.change_book_status.assert_called_once_with(user_book, STATUS_READ)


def test_partial_progress_with_no_edition_does_not_mark_read():
    user_book = {"id": 12, "status_id": STATUS_READING, "edition": None,
                 "user_book_reads": [{"id": 34}]}
    client = _client(user_book)

    client.update_reading_progress({"hardcover-id": "123"}, 40)

    assert all(call.args[1] != STATUS_READ
               for call in client.change_book_status.call_args_list)


# ── HardcoverClient.mark_book_read ───────────────────────────────────────────

def test_mark_read_adds_a_book_hardcover_does_not_have_yet_as_read():
    client = _client(None)

    assert client.mark_book_read({"hardcover-id": "123"})

    client.add_book.assert_called_once_with({"hardcover-id": "123"}, status=STATUS_READ)
    client.change_book_status.assert_not_called()


@pytest.mark.parametrize("status", [WTR, STATUS_READING])
def test_mark_read_moves_a_listed_book_to_read(status):
    user_book = {"id": 12, "status_id": status}
    client = _client(user_book)

    assert client.mark_book_read({"hardcover-id": "123"})["status_id"] == STATUS_READ

    client.change_book_status.assert_called_once_with(user_book, STATUS_READ)
    client.add_book.assert_not_called()


def test_mark_read_leaves_a_book_already_read_alone():
    client = _client({"id": 12, "status_id": STATUS_READ})

    assert client.mark_book_read({"hardcover-id": "123"})

    client.change_book_status.assert_not_called()
    client.add_book.assert_not_called()


def test_mark_read_reports_failure_when_hardcover_rejects_the_change():
    client = _client({"id": 12, "status_id": WTR})
    client.change_book_status = MagicMock(return_value={})

    assert not client.mark_book_read({"hardcover-id": "123"})


# ── every manual read-status change queues the push ──────────────────────────

@pytest.fixture
def default_install(monkeypatch):
    """The real ``helper.edit_book_read_status`` on a default install
    (no Calibre read column), with WorkerThread.add recorded."""
    from cps import helper

    engine = create_engine("sqlite:///:memory:")
    ub.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    queued = []
    user = SimpleNamespace(id=7, name="reader", hardcover_token="tok-7")
    monkeypatch.setattr(helper.config, "config_read_column", 0, raising=False)
    monkeypatch.setattr(helper.config, "hardcover_sync_enabled", lambda: True,
                        raising=False)
    monkeypatch.setattr(helper, "current_user", user)
    monkeypatch.setattr(helper.ub, "session", session)
    monkeypatch.setattr(helper.ub, "session_commit", lambda *a, **k: True)
    monkeypatch.setattr(helper.WorkerThread, "add",
                        staticmethod(lambda owner, task, **k: queued.append((owner, task))))
    return SimpleNamespace(helper=helper, session=session, queued=queued, user=user,
                           monkeypatch=monkeypatch)


def _queued_books(env):
    from cps.tasks.hardcover_sync import TaskHardcoverMarkRead
    assert all(isinstance(task, TaskHardcoverMarkRead) for _, task in env.queued)
    return [(owner, task.token, task.book_ids) for owner, task in env.queued]


@pytest.mark.parametrize("read_status", [True, None])
def test_marking_read_queues_a_hardcover_mark_read(default_install, read_status):
    env = default_install

    assert env.helper.edit_book_read_status(42, read_status) == ""

    assert _queued_books(env) == [("reader", "tok-7", [42])]


def test_marking_unread_sends_nothing_to_hardcover(default_install):
    env = default_install
    env.helper.edit_book_read_status(42, True)
    env.queued.clear()

    assert env.helper.edit_book_read_status(42, False) == ""

    assert env.queued == []


def test_nothing_is_queued_when_hardcover_sync_is_off(default_install):
    env = default_install
    env.monkeypatch.setattr(env.helper.config, "hardcover_sync_enabled", lambda: False,
                            raising=False)

    env.helper.edit_book_read_status(42, True)

    assert env.queued == []


def test_nothing_is_queued_without_the_users_own_token(default_install):
    env = default_install
    env.user.hardcover_token = None

    env.helper.edit_book_read_status(42, True)

    assert env.queued == []


def test_a_book_blacklisted_for_progress_is_not_pushed(default_install):
    env = default_install
    env.session.add(ub.HardcoverBookBlacklist(book_id=42, blacklist_reading_progress=True))
    env.session.commit()

    env.helper.edit_book_read_status(42, True)
    env.helper.queue_hardcover_mark_read([42, 43])

    assert _queued_books(env) == [("reader", "tok-7", [43])]


def test_a_bulk_caller_can_queue_one_task_for_the_selection(default_install):
    env = default_install
    for book_id in (1, 2, 3):
        assert env.helper.edit_book_read_status(book_id, True, sync_hardcover=False) == ""
    assert env.queued == []

    env.helper.queue_hardcover_mark_read([1, 2, 3])

    assert _queued_books(env) == [("reader", "tok-7", [1, 2, 3])]


# ── the worker task ──────────────────────────────────────────────────────────

class _FakeCalibreDB:
    def __init__(self, books):
        books_by_id = {book.id: book for book in books}

        class _Query:
            def filter(self, expr):
                self.book_id = expr.right.value
                return self

            def one_or_none(self):
                return books_by_id.get(self.book_id)

        self.session = SimpleNamespace(query=lambda _model: _Query(), close=lambda: None)


def _book(book_id, **identifiers):
    return SimpleNamespace(id=book_id, identifiers=[
        SimpleNamespace(type=key, val=val) for key, val in identifiers.items()])


def test_the_task_marks_each_book_read_and_skips_books_hardcover_cannot_match(monkeypatch):
    from cps.tasks import hardcover_sync

    calls = []

    class _Client:
        def __init__(self, token):
            assert token == "tok-7"

        def mark_book_read(self, identifiers):
            calls.append(identifiers)
            return {"id": 1, "status_id": STATUS_READ}

    books = [_book(1, **{"hardcover-id": "11", "isbn": "x"}), _book(2, isbn="y")]
    monkeypatch.setattr(hardcover_sync.hardcover, "HardcoverClient", _Client)
    monkeypatch.setattr(hardcover_sync.db, "CalibreDB",
                        lambda **k: _FakeCalibreDB(books))
    monkeypatch.setattr(hardcover_sync, "INTER_BOOK_DELAY", 0)
    task = hardcover_sync.TaskHardcoverMarkRead("tok-7", [1, 2, 3])
    task._handleSuccess = MagicMock()
    task._handleError = MagicMock()

    task.run(worker_thread=None)

    assert calls == [{"hardcover-id": "11"}]
    assert (task.synced, task.skipped_no_identifiers, task.errors) == (1, 2, 0)
    task._handleSuccess.assert_called_once()
    task._handleError.assert_not_called()
