# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression coverage for fork #1857's poisoned KEPUB backfill session."""

from types import SimpleNamespace

import pytest

from cps.services.worker import STAT_FAIL


pytestmark = pytest.mark.unit


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def distinct(self):
        return self

    def all(self):
        return list(self._rows)


def _wire_common(monkeypatch, kepub_backfill, calibre_db, book_ids):
    class AppSession:
        def query(self, *_args):
            return _Query([(book_id,) for book_id in book_ids])

        def close(self):
            pass

    saved = []
    monkeypatch.setattr(kepub_backfill.ub, "get_new_session_instance", AppSession)
    monkeypatch.setattr(kepub_backfill.db, "CalibreDB", calibre_db)
    monkeypatch.setattr(kepub_backfill, "get_epub_layout", lambda *_args: None)
    monkeypatch.setattr(
        kepub_backfill.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(
        kepub_backfill.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(
        kepub_backfill.config, "config_kobo_kepub_backfill_completed", False,
        raising=False)
    monkeypatch.setattr(
        kepub_backfill.config, "get_book_path", lambda: "/books", raising=False)
    monkeypatch.setattr(
        kepub_backfill.config,
        "save",
        lambda: saved.append(
            kepub_backfill.config.config_kobo_kepub_backfill_completed),
        raising=False,
    )
    return saved


def test_closed_session_failure_rebuilds_and_processes_later_books(monkeypatch):
    """Book k poisons one Session; k+1..n run on a fresh CalibreDB/Session."""
    from cps.tasks import kepub_backfill

    instances = []
    book_queries = []

    class Session:
        def __init__(self, number):
            self.number = number
            self.is_active = True
            self.rollback_calls = 0

        def rollback(self):
            self.rollback_calls += 1
            self.is_active = True

    class CalibreDB:
        def __init__(self, **_kwargs):
            self.number = len(instances) + 1
            self.session = Session(self.number)
            instances.append(self)

        def get_book(self, book_id):
            book_queries.append((self.number, book_id))
            if self.number == 1 and book_id == 2:
                self.session.is_active = False
                raise RuntimeError(
                    "Can't reconnect until invalid transaction is rolled back")
            return SimpleNamespace(
                id=book_id, path=str(book_id), title=str(book_id))

        def get_book_format(self, _book_id, fmt):
            if fmt == "EPUB":
                return SimpleNamespace(format="EPUB", name="book")
            return None

    converted = []

    class Conversion:
        def __init__(self, _path, book_id, *_args):
            self.book_id = book_id
            self.error = None

        def _convert_ebook_format(self):
            converted.append(self.book_id)
            return "book.kepub"

    saved = _wire_common(monkeypatch, kepub_backfill, CalibreDB, [1, 2, 3, 4])
    monkeypatch.setattr(kepub_backfill, "TaskConvert", Conversion)
    per_book_errors = []
    monkeypatch.setattr(
        kepub_backfill.log, "error_or_exception", per_book_errors.append)

    task = kepub_backfill.TaskKepubBackfill()
    task.run(None)

    assert converted == [1, 3, 4]
    assert book_queries == [(1, 1), (1, 2), (2, 2), (2, 3), (2, 4)]
    assert len(instances) == 2
    assert instances[0].session is None
    assert task.processed == 4
    assert task.converted == 3
    assert task.skipped == 0
    assert task.failed == 1
    assert str(task.message) == "4/4 processed: 3 converted, 0 skipped, 1 failed"
    assert str(task.error) == (
        "KEPUB backfill finished with failures; "
        "4/4 processed: 3 converted, 0 skipped, 1 failed")
    assert task.stat == STAT_FAIL
    assert saved == [False]
    assert len(per_book_errors) == 1
    assert "Can't reconnect until invalid transaction is rolled back" in per_book_errors[0]


def test_missing_calibre_schema_fails_metadata_probe_and_aborts_bounded(monkeypatch):
    """SELECT 1 is healthy, but a missing Books table stops after three probes."""
    from cps.tasks import kepub_backfill

    constructor_calls = []
    queried_books = []
    select_one_calls = []
    converted = []

    class Session:
        def __init__(self):
            self.is_active = True

        def rollback(self):
            pass

        def execute(self, _statement):
            select_one_calls.append(True)
            return SimpleNamespace(scalar=lambda: 1)

    class CalibreDB:
        def __init__(self, **_kwargs):
            constructor_calls.append(len(constructor_calls) + 1)
            self.number = len(constructor_calls)
            self.session = Session()

        def get_book(self, book_id):
            queried_books.append((self.number, book_id))
            if self.number == 1 and book_id == 1:
                return SimpleNamespace(id=book_id, path=str(book_id), title=str(book_id))
            raise RuntimeError("no such table: books")

        def get_book_format(self, _book_id, fmt):
            if fmt == "EPUB":
                return SimpleNamespace(format="EPUB", name="book")
            return None

    class Conversion:
        def __init__(self, _path, book_id, *_args):
            self.book_id = book_id
            self.error = None

        def _convert_ebook_format(self):
            converted.append(self.book_id)
            return "book.kepub"

    saved = _wire_common(monkeypatch, kepub_backfill, CalibreDB, [1, 2, 3, 4, 5])
    monkeypatch.setattr(
        kepub_backfill.config,
        "config_kobo_kepub_backfill_completed",
        True,
        raising=False,
    )
    monkeypatch.setattr(kepub_backfill, "TaskConvert", Conversion)
    terminal_logs = []

    def capture_error(message, *args, **_kwargs):
        terminal_logs.append(message % args if args else str(message))

    monkeypatch.setattr(kepub_backfill.log, "error", capture_error)

    task = kepub_backfill.TaskKepubBackfill()
    task.run(None)

    assert converted == [1]
    assert constructor_calls == [1, 2, 3, 4]
    assert queried_books == [(1, 1), (1, 2), (2, 2), (3, 2), (4, 2)]
    assert select_one_calls == []  # It would pass, but is no longer the health probe.
    assert task.processed == 2
    assert task.converted == 1
    assert task.skipped == 0
    assert task.failed == 1
    assert str(task.message) == "2/5 processed: 1 converted, 0 skipped, 1 failed"
    assert "aborted after 3 consecutive Calibre metadata probe failures" in str(task.error)
    assert "2/5 processed: 1 converted, 0 skipped, 1 failed" in str(task.error)
    assert task.stat == STAT_FAIL
    assert kepub_backfill.config.config_kobo_kepub_backfill_completed is False
    assert saved == [False]
    assert sum("aborted after 3 consecutive" in line for line in terminal_logs) == 1
    assert not any("book 3" in line or "book 4" in line or "book 5" in line
                   for line in terminal_logs)


def test_post_rebuild_database_failures_trip_the_consecutive_breaker(monkeypatch):
    """A passing schema probe does not reset repeated real-query failures."""
    from cps.tasks import kepub_backfill

    instances = []
    queried_books = []
    converted = []

    class Session:
        is_active = True

        def rollback(self):
            pass

    class CalibreDB:
        def __init__(self, **_kwargs):
            self.number = len(instances) + 1
            self.session = Session()
            self.probe_passed = False
            instances.append(self)

        def get_book(self, book_id):
            queried_books.append((self.number, book_id))
            if self.number == 1 and book_id == 1:
                return SimpleNamespace(id=book_id, path=str(book_id), title=str(book_id))
            if self.number > 1 and not self.probe_passed:
                self.probe_passed = True
                return SimpleNamespace(id=book_id, path=str(book_id), title=str(book_id))
            raise RuntimeError("real metadata query failed after a healthy probe")

        def get_book_format(self, _book_id, fmt):
            if fmt == "EPUB":
                return SimpleNamespace(format="EPUB", name="book")
            return None

    class Conversion:
        def __init__(self, _path, book_id, *_args):
            self.book_id = book_id
            self.error = None

        def _convert_ebook_format(self):
            converted.append(self.book_id)
            return "book.kepub"

    saved = _wire_common(monkeypatch, kepub_backfill, CalibreDB, [1, 2, 3, 4, 5, 6])
    monkeypatch.setattr(kepub_backfill, "TaskConvert", Conversion)

    task = kepub_backfill.TaskKepubBackfill()
    task.run(None)

    assert converted == [1]
    assert len(instances) == 3
    assert queried_books == [
        (1, 1), (1, 2),
        (2, 2), (2, 3),
        (3, 3), (3, 4),
    ]
    assert task.processed == 4
    assert task.converted == 1
    assert task.skipped == 0
    assert task.failed == 3
    assert str(task.message) == "4/6 processed: 1 converted, 0 skipped, 3 failed"
    assert "aborted after 3 consecutive Calibre metadata database failures" in str(task.error)
    assert task.stat == STAT_FAIL
    assert kepub_backfill.config.config_kobo_kepub_backfill_completed is False
    assert saved == [False]


def test_archive_failure_is_per_book_and_does_not_rebuild_database(monkeypatch):
    """Archive/conversion trouble continues on the same healthy Session."""
    from cps.tasks import kepub_backfill

    instances = []
    converted = []

    class Session:
        is_active = True

    class CalibreDB:
        def __init__(self, **_kwargs):
            self.session = Session()
            instances.append(self)

        def get_book(self, book_id):
            return SimpleNamespace(id=book_id, path=str(book_id), title=str(book_id))

        def get_book_format(self, _book_id, fmt):
            if fmt == "EPUB":
                return SimpleNamespace(format="EPUB", name="book")
            return None

    class Conversion:
        def __init__(self, _path, book_id, *_args):
            self.book_id = book_id
            self.error = None

        def _convert_ebook_format(self):
            converted.append(self.book_id)
            return "book.kepub"

    saved = _wire_common(monkeypatch, kepub_backfill, CalibreDB, [1, 2])
    monkeypatch.setattr(kepub_backfill, "TaskConvert", Conversion)

    def layout(book, _epub):
        if book.id == 1:
            raise OSError("broken EPUB archive")
        return None

    monkeypatch.setattr(kepub_backfill, "get_epub_layout", layout)

    task = kepub_backfill.TaskKepubBackfill()
    task.run(None)

    assert len(instances) == 1
    assert converted == [2]
    assert task.processed == 2
    assert task.converted == 1
    assert task.failed == 1
    assert task.stat == STAT_FAIL
    assert saved == [False]
