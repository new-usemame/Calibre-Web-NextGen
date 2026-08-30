# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Regression coverage for fork #1900's non-terminal thumbnail run."""

import time
from types import SimpleNamespace

import pytest

from cps.services.worker import STAT_FAIL, STAT_FINISH_SUCCESS


pytestmark = pytest.mark.unit


class _Session:
    def remove(self):
        pass


class _Cache:
    pass


def _task(monkeypatch, thumbnail, books):
    monkeypatch.setattr(thumbnail.ub, "get_new_session_instance", _Session)
    monkeypatch.setattr(thumbnail.fs, "FileSystem", _Cache)
    monkeypatch.setattr(thumbnail, "use_IM", True)
    task = thumbnail.TaskGenerateCoverThumbnails()
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
    monkeypatch.setattr(
        task,
        "create_book_cover_thumbnails",
        lambda _book: time.sleep(0.25),
    )

    started = time.monotonic()
    with pytest.raises(thumbnail.CoverThumbnailTimeout):
        task._run_cover_with_timeout(SimpleNamespace(id=7))

    assert time.monotonic() - started < 0.15


def test_start_and_finish_logs_publish_terminal_counts(monkeypatch):
    from cps.tasks import thumbnail

    task = _task(monkeypatch, thumbnail, [SimpleNamespace(id=1)])
    monkeypatch.setattr(task, "_run_cover_with_timeout", lambda _book: 4)
    info_calls = []
    monkeypatch.setattr(task.log, "info", lambda *args: info_calls.append(args))

    task.run(None)

    rendered = [call[0] % call[1:] for call in info_calls]
    assert sum("generation started" in line for line in rendered) == 1
    assert sum("generation finished" in line for line in rendered) == 1
    assert "total=1" in rendered[0]
    assert "generated=1" in rendered[-1]
    assert "skipped=0" in rendered[-1]
    assert "failed=0" in rendered[-1]
