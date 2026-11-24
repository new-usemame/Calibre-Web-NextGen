# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for fork #1900's non-terminal thumbnail run."""

import logging
from threading import Event
from types import SimpleNamespace

import pytest

from cps.services.worker import STAT_FAIL, STAT_FINISH_SUCCESS


pytestmark = pytest.mark.unit


class _Session:
    def remove(self):
        pass


class _Cache:
    pass


def _task(monkeypatch, thumbnail, books, *, book_id=-1):
    monkeypatch.setattr(thumbnail.ub, "get_new_session_instance", _Session)
    monkeypatch.setattr(thumbnail.fs, "FileSystem", _Cache)
    monkeypatch.setattr(thumbnail, "use_IM", True)
    task = thumbnail.TaskGenerateCoverThumbnails(book_id=book_id)
    monkeypatch.setattr(task, "get_cover_sources", lambda: list(books))
    return task


def test_one_timed_out_cover_does_not_prevent_later_books(monkeypatch):
    from cps.tasks import thumbnail

    books = [SimpleNamespace(id=value) for value in (1, 2, 3)]
    task = _task(monkeypatch, thumbnail, books)
    attempted = []

    def generate(book):
        attempted.append(book.id)
        if book.id == 1:
            raise thumbnail.CoverThumbnailTimeout("stuck cover")
        return 2 if book.id == 2 else 0

    monkeypatch.setattr(task, "_run_cover_with_timeout", generate)
    task.run(None)

    assert attempted == [1, 2, 3]
    assert (task.processed, task.generated, task.skipped, task.failed) == (3, 1, 1, 1)
    assert str(task.message) == "3/3 processed: 1 generated, 1 skipped, 1 failed"
    assert "finished with failures" in str(task.error)
    assert task.stat == STAT_FAIL


def test_three_consecutive_timeouts_trip_the_systemic_breaker(monkeypatch):
    from cps.tasks import thumbnail

    books = [SimpleNamespace(id=value) for value in range(1, 6)]
    task = _task(monkeypatch, thumbnail, books)
    attempted = []

    def timeout(book):
        attempted.append(book.id)
        raise thumbnail.CoverThumbnailTimeout("stuck cover")

    monkeypatch.setattr(task, "_run_cover_with_timeout", timeout)
    task.run(None)

    assert attempted == [1, 2, 3]
    assert (task.processed, task.generated, task.skipped, task.failed) == (3, 0, 0, 3)
    assert "aborted after 3 consecutive cover timeouts" in str(task.error)
    assert "3/5 processed" in str(task.error)
    assert task.stat == STAT_FAIL


def test_per_item_failure_is_contained_but_terminal_status_is_honest(monkeypatch):
    from cps.tasks import thumbnail

    books = [SimpleNamespace(id=value) for value in (1, 2)]
    task = _task(monkeypatch, thumbnail, books)

    def generate(book):
        if book.id == 1:
            raise OSError("broken cover")
        return 1

    monkeypatch.setattr(task, "_run_cover_with_timeout", generate)
    task.run(None)

    assert (task.processed, task.generated, task.skipped, task.failed) == (2, 1, 0, 1)
    assert task.progress == 1
    assert task.stat == STAT_FAIL
    assert task.done_event.is_set()


def test_all_skipped_covers_finish_successfully_with_counts(monkeypatch):
    from cps.tasks import thumbnail

    books = [SimpleNamespace(id=value) for value in (1, 2)]
    task = _task(monkeypatch, thumbnail, books)
    monkeypatch.setattr(task, "_run_cover_with_timeout", lambda _book: 0)

    task.run(None)

    assert (task.processed, task.generated, task.skipped, task.failed) == (2, 0, 2, 0)
    assert str(task.message) == "2/2 processed: 0 generated, 2 skipped, 0 failed"
    assert task.stat == STAT_FINISH_SUCCESS
    assert task.self_cleanup is True


def test_real_timeout_wrapper_returns_control_to_the_worker(monkeypatch):
    from cps.tasks import thumbnail

    task = _task(monkeypatch, thumbnail, [])
    monkeypatch.setattr(thumbnail, "_COVER_TIMEOUT_SECONDS", 0.02)
    release_cover = Event()
    monkeypatch.setattr(
        task,
        "create_book_cover_thumbnails",
        lambda _book: release_cover.wait(timeout=5),
    )

    try:
        with pytest.raises(thumbnail.CoverThumbnailTimeout):
            task._run_cover_with_timeout(SimpleNamespace(id=7))
    finally:
        release_cover.set()


@pytest.mark.parametrize(("book_id", "level"), [(1, logging.DEBUG), (-1, logging.INFO)])
def test_successful_cover_logs_keep_batch_summaries_visible(monkeypatch, caplog, book_id, level):
    from cps.tasks import thumbnail

    task = _task(monkeypatch, thumbnail, [SimpleNamespace(id=1)], book_id=book_id)
    monkeypatch.setattr(task, "_run_cover_with_timeout", lambda _book: 4)

    with caplog.at_level(logging.DEBUG, logger=task.log.name):
        task.run(None)

    records = [record for record in caplog.records if record.name == task.log.name
               and record.getMessage().startswith("Cover thumbnail generation ")]
    assert [record.levelno for record in records] == [level, level]
    assert "started: total=1" in records[0].getMessage()
    assert "finished: status=success" in records[1].getMessage()
    assert "generated=1, skipped=0, failed=0" in records[1].getMessage()


def test_single_book_cover_failure_stays_visible_at_info(monkeypatch, caplog):
    from cps.tasks import thumbnail

    task = _task(monkeypatch, thumbnail, [SimpleNamespace(id=1)], book_id=1)

    def generate(_book):
        raise OSError("broken cover")

    monkeypatch.setattr(task, "_run_cover_with_timeout", generate)

    with caplog.at_level(logging.INFO, logger=task.log.name):
        task.run(None)

    assert task.stat == STAT_FAIL
    assert any(record.levelno >= logging.WARNING and "broken cover" in record.getMessage()
               for record in caplog.records)
    assert "finished: status=failed" in caplog.text
