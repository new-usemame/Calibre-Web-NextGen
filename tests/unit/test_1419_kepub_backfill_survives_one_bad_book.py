# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression pins for fork #1419 — one bad book must not abort the KEPUB
migration, and a cancelled backfill must not wedge KEPUB delivery.

The KEPUB backfill ships **on by default** and runs unattended at startup, so
its failure modes land on every upgrading user without anyone asking for it.
Three defects made that dangerous, all found by the pre-release refuter loop:

1. Only ``_convert_ebook_format()`` sat inside the per-book ``try``. Reading the
   book row, reading its formats and ``get_epub_layout`` were all outside it —
   and ``get_epub_layout`` raises ``zipfile.BadZipFile`` on a truncated archive,
   which is *not* an ``OSError`` and so was not caught in ``cps/epub.py``
   either. One corrupt EPUB anywhere in the synced set therefore aborted the
   whole loop, converted **zero** books, and — because the exception escaped
   before the completed flag was set — re-queued the identical doomed run on
   every boot, forever, on a ``hidden=True`` task with no UI trace.

2. A task cancelled while still queued never runs, so the ``finally`` in
   ``run()`` that clears ``_pending`` never fired. The latch then made
   ``is_kepub_backfill_pending()`` true for the life of the process, which
   silently downgraded every Kobo KEPUB download to EPUB while still telling the
   device the format was KEPUB.

3. The completed flag was set before ``self.failed`` was consulted, so a run
   where *everything* failed (read-only books volume, a mount that arrived late)
   was checked off as a finished migration and never retried.
"""

import zipfile
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def distinct(self):
        return self

    def all(self):
        return self._rows


def _app_session(rows):
    class AppSession:
        def query(self, *_):
            return _Query(rows)

        def close(self):
            pass

    return AppSession


def _calibre_db(get_book=None):
    class CalibreDB:
        def __init__(self, **_):
            self.session = SimpleNamespace(close=lambda: None)

        def get_book(self, book_id):
            if get_book is not None:
                return get_book(book_id)
            return SimpleNamespace(id=book_id, path=str(book_id), title=str(book_id))

        def get_book_format(self, book_id, fmt):
            return SimpleNamespace(format=fmt, name="book") if fmt == "EPUB" else None

    return CalibreDB


def _wire(monkeypatch, kepub_backfill, rows, conversion, layout, get_book=None):
    saved = []
    monkeypatch.setattr(kepub_backfill.ub, "get_new_session_instance", _app_session(rows))
    monkeypatch.setattr(kepub_backfill.db, "CalibreDB", _calibre_db(get_book))
    monkeypatch.setattr(kepub_backfill, "TaskConvert", conversion)
    monkeypatch.setattr(kepub_backfill, "get_epub_layout", layout)
    monkeypatch.setattr(kepub_backfill.config, "config_use_google_drive", False, raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(
        kepub_backfill.config, "config_kobo_kepub_backfill_completed", False, raising=False)
    monkeypatch.setattr(kepub_backfill.config, "get_book_path", lambda: "/books", raising=False)
    monkeypatch.setattr(kepub_backfill.config, "save", lambda: saved.append(True), raising=False)
    return saved


def _conversion(attempted, fail_ids=()):
    class Conversion:
        def __init__(self, _path, book_id, *_args):
            self.book_id, self.error = book_id, None

        def _convert_ebook_format(self):
            attempted.append(self.book_id)
            if self.book_id in fail_ids:
                self.error = "boom"
                return None
            return "book.kepub"

    return Conversion


def test_corrupt_epub_does_not_abort_the_whole_migration(monkeypatch):
    """A truncated archive on book 1 must not cost books 2 and 3 their KEPUBs."""
    from cps.tasks import kepub_backfill

    attempted = []

    def layout(book, _data):
        if book.id == 1:
            raise zipfile.BadZipFile("File is not a zip file")
        return None

    saved = _wire(monkeypatch, kepub_backfill, [(1,), (2,), (3,)],
                  _conversion(attempted), layout)

    task = kepub_backfill.TaskKepubBackfill()
    task.run(None)

    # Pre-fix this was [] -- the exception escaped the loop on the first book.
    assert attempted == [2, 3]
    assert task.converted == 2
    assert task.failed == 1
    # Pre-fix this stayed False, so every boot re-queued the same doomed run.
    assert kepub_backfill.config.config_kobo_kepub_backfill_completed is True
    assert saved == [True]


def test_unreadable_book_row_does_not_abort_the_whole_migration(monkeypatch):
    """get_book/get_book_format can raise on a busy database, not just convert."""
    from cps.tasks import kepub_backfill

    attempted = []

    def get_book(book_id):
        if book_id == 1:
            raise RuntimeError("database is locked")
        return SimpleNamespace(id=book_id, path=str(book_id), title=str(book_id))

    _wire(monkeypatch, kepub_backfill, [(1,), (2,)],
          _conversion(attempted), lambda *_: None, get_book=get_book)

    task = kepub_backfill.TaskKepubBackfill()
    task.run(None)

    assert attempted == [2]
    assert task.converted == 1
    assert task.failed == 1
    assert kepub_backfill.config.config_kobo_kepub_backfill_completed is True


def test_a_run_that_converts_nothing_is_not_marked_completed(monkeypatch):
    """Total failure is a broken environment, not a finished migration."""
    from cps.tasks import kepub_backfill

    attempted = []
    _wire(monkeypatch, kepub_backfill, [(1,), (2,)],
          _conversion(attempted, fail_ids=(1, 2)), lambda *_: None)

    task = kepub_backfill.TaskKepubBackfill()
    task.run(None)

    assert task.converted == 0
    assert task.failed == 2
    # Pre-fix this was True: the library kept no KEPUBs and never retried.
    assert kepub_backfill.config.config_kobo_kepub_backfill_completed is False


def test_partial_failure_still_marks_completed(monkeypatch):
    """Preserved behaviour: some progress means the migration ran."""
    from cps.tasks import kepub_backfill

    attempted = []
    _wire(monkeypatch, kepub_backfill, [(1,), (2,)],
          _conversion(attempted, fail_ids=(1,)), lambda *_: None)

    task = kepub_backfill.TaskKepubBackfill()
    task.run(None)

    assert task.converted == 1 and task.failed == 1
    assert kepub_backfill.config.config_kobo_kepub_backfill_completed is True


def test_cancel_while_queued_clears_the_pending_latch(monkeypatch):
    """A task cancelled before it runs never reaches run()'s finally."""
    from cps.tasks import kepub_backfill
    from cps.services.worker import STAT_CANCELLED

    monkeypatch.setattr(kepub_backfill, "_pending", True, raising=False)
    task = kepub_backfill.TaskKepubBackfill()

    task.stat = STAT_CANCELLED

    # Pre-fix this stayed True for the life of the process, so every Kobo KEPUB
    # download was silently served as EPUB and the admin button reported a bogus
    # "check your kepubify path".
    assert kepub_backfill.is_kepub_backfill_pending() is False


def test_failed_enqueue_does_not_leave_the_latch_set(monkeypatch):
    """Nothing was queued, so nothing would ever clear it."""
    from cps.tasks import kepub_backfill

    monkeypatch.setattr(kepub_backfill, "_pending", False, raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_kobo_prefer_kepub", True, raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(kepub_backfill.config, "config_use_google_drive", False, raising=False)

    def boom(*_args, **_kwargs):
        raise RuntimeError("worker is down")

    monkeypatch.setattr(kepub_backfill.WorkerThread, "add", staticmethod(boom))

    with pytest.raises(RuntimeError):
        kepub_backfill.enqueue_kepub_backfill()

    assert kepub_backfill.is_kepub_backfill_pending() is False


def test_get_epub_layout_returns_none_for_a_corrupt_archive(monkeypatch, tmp_path):
    """The contract is 'None when unparseable' -- BadZipFile is exactly that."""
    from cps import epub as epub_module

    book_dir = tmp_path / "1"
    book_dir.mkdir()
    (book_dir / "book.epub").write_bytes(b"this is not a zip archive at all")

    monkeypatch.setattr(epub_module.config, "get_book_path", lambda: str(tmp_path), raising=False)
    book = SimpleNamespace(id=1, path="1")
    book_data = SimpleNamespace(name="book", format="EPUB")

    # Pre-fix this raised zipfile.BadZipFile straight through the handler,
    # because BadZipFile subclasses Exception rather than OSError.
    assert epub_module.get_epub_layout(book, book_data) is None
