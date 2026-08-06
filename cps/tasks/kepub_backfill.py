# -*- coding: utf-8 -*-
"""Bounded, idempotent KEPUB production for books already delivered to Kobo."""

import os
import threading

from flask_babel import lazy_gettext as N_

from .. import config, db, helper, logger, ub
from ..epub import get_epub_layout
from ..services.worker import (CalibreTask, STAT_CANCELLED, STAT_ENDED, STAT_FAIL,
                               STAT_FINISH_SUCCESS, WorkerThread)
from .convert import TaskConvert

log = logger.create()
# Reentrant: the stat setter below can take this lock from an arbitrary
# attribute assignment, so a plain Lock would make `task.stat = ...` while
# holding it deadlock forever. Nothing does that today; this costs nothing.
_enqueue_lock = threading.RLock()
_pending = False
# Which task instance owns the latch. Without this, ANY instance reaching a
# terminal state frees a latch a DIFFERENT instance is holding -- reachable
# three ways: a finishing run releasing it before its own `finally` runs again,
# a cancel of an already-finished task still retained in `dequeued` (end_task
# has no terminal-state guard), and the window between a mid-run cancel and the
# loop noticing it. A falsely-free latch puts a blocking 25s conversion on the
# Kobo download request path, which is the exact thing the latch prevents.
_pending_owner = None

# Terminal states. `run()` clears the pending latch in its own `finally`, but a
# task cancelled while it is still queued never runs at all -- the worker only
# calls start() on a STAT_WAITING task. Clearing on any terminal state covers
# that path too, so a cancel cannot leave the latch stuck on for the lifetime of
# the process (which silently downgraded every Kobo KEPUB download to EPUB).
_TERMINAL_STATS = (STAT_FAIL, STAT_FINISH_SUCCESS, STAT_ENDED, STAT_CANCELLED)


def _clear_pending(task):
    """Release the latch only if `task` is the instance that took it."""
    global _pending, _pending_owner
    with _enqueue_lock:
        if _pending_owner is not None and _pending_owner is not task:
            return
        _pending = False
        _pending_owner = None


class TaskKepubBackfill(CalibreTask):
    def __init__(self):
        super().__init__(N_(u"Convert Kobo books missing KEPUB"))
        self.converted = 0
        self.skipped = 0
        self.failed = 0

    def run(self, worker_thread):
        global _pending
        try:
            if config.config_use_google_drive:
                log.info("KEPUB backfill skipped: Google Drive libraries are not supported")
                self._handleSuccess()
                return
            if not config.config_kepubifypath:
                self._handleError(N_(u"Kepubify is not configured"))
                return

            app_session = ub.get_new_session_instance()
            try:
                book_ids = [row[0] for row in app_session.query(ub.KoboSyncedBooks.book_id).distinct().all()]
            finally:
                app_session.close()

            local_db = db.CalibreDB(expire_on_commit=False, init=True)
            try:
                total = len(book_ids)
                for index, book_id in enumerate(book_ids):
                    if self.stat == STAT_ENDED:
                        return
                    # The guard covers the whole per-book body, not just the
                    # conversion. Reading the book row and its formats can raise
                    # on a busy database, and get_epub_layout raises on an
                    # archive it cannot open -- before this, any one of those
                    # escaped the loop entirely, skipped the completed flag
                    # below, and left the hidden startup task re-queuing the
                    # same run that converts nothing on every single boot.
                    try:
                        self._backfill_one_book(local_db, book_id)
                    except Exception as error:
                        self.failed += 1
                        log.error_or_exception(
                            "KEPUB backfill failed for book {}: {}".format(book_id, error))
                    self.progress = (index + 1) / total if total else 1
            finally:
                # CalibreDB.session is documented as possibly None (cps/db.py
                # "Don't raise exception - let caller handle AttributeError"), and
                # a raise here both escapes run() and masks whatever the try body
                # was actually failing on -- so the task would report "'NoneType'
                # has no attribute 'close'" instead of the real cause.
                try:
                    local_db.session.close()
                except Exception as error:
                    log.error("KEPUB backfill could not close its database session: %s", error)

            # The startup backfill is a bounded migration attempt. Individual
            # failures remain visible on the task, but must not make every boot
            # repeat the same bulk conversion against an unwritable library.
            #
            # A run that converted nothing at all and failed at least once is a
            # broken environment (read-only books volume, a mount that arrived
            # late), not a finished migration. Checking it off would strand the
            # library with no KEPUBs, no retry and -- because the startup task is
            # hidden -- no explanation. Leave the flag clear so the next boot
            # tries again.
            if self.converted or not self.failed:
                # Persist first, then flip the in-memory flag: a save() that
                # raises would otherwise leave this process believing the
                # migration is done while the next boot re-reads False.
                config.config_kobo_kepub_backfill_completed = True
                try:
                    config.save()
                except Exception:
                    config.config_kobo_kepub_backfill_completed = False
                    raise
            if self.failed:
                self._handleError(N_(u"%(count)d KEPUB conversion(s) failed", count=self.failed))
                return
            self._handleSuccess()
        finally:
            _clear_pending(self)

    def _backfill_one_book(self, local_db, book_id):
        """Convert one book, or record why it was skipped. Raises on I/O trouble."""
        book = local_db.get_book(book_id)
        if not book:
            self.skipped += 1
            return
        kepub = local_db.get_book_format(book_id, "KEPUB")
        epub = local_db.get_book_format(book_id, "EPUB")
        if kepub or not epub:
            self.skipped += 1
            return
        if get_epub_layout(book, epub) == "pre-paginated":
            self.skipped += 1
            return

        file_path = os.path.join(config.get_book_path(), book.path, epub.name)
        settings = {"old_book_format": "EPUB", "new_book_format": "KEPUB"}
        conversion = TaskConvert(file_path, book_id, "EPUB -> KEPUB", settings, None)
        if conversion._convert_ebook_format():
            self.converted += 1
        else:
            self.failed += 1
            log.error("KEPUB backfill failed for book %d: %s", book_id, conversion.error)

    @property
    def name(self):
        # Startup tasks are formatted before any Flask/Babel context exists.
        return "Kobo KEPUB backfill"

    @property
    def is_cancellable(self):
        return True

    @CalibreTask.stat.setter
    def stat(self, value):
        CalibreTask.stat.fset(self, value)
        if value in _TERMINAL_STATS:
            _clear_pending(self)


def enqueue_kepub_backfill(user="System", hidden=False):
    """Queue at most one composite scan/conversion task at a time."""
    global _pending, _pending_owner
    if (not config.config_kobo_prefer_kepub or not config.config_kepubifypath
            or config.config_use_google_drive):
        return False
    task = TaskKepubBackfill()
    with _enqueue_lock:
        if _pending:
            return False
        _pending = True
        _pending_owner = task
    try:
        WorkerThread.add(user, task, hidden=hidden)
    except Exception:
        # Nothing is queued, so nothing will ever clear the latch. Leaving it set
        # makes every later enqueue return False and tells the admin their
        # kepubify path is wrong, which is the one thing it is not.
        _clear_pending(task)
        raise
    return True


def enqueue_startup_kepub_backfill():
    if not config.config_kobo_kepub_backfill_completed:
        return enqueue_kepub_backfill(hidden=True)
    return False


def is_kepub_backfill_pending():
    """Return whether the composite backfill is queued or running."""
    with _enqueue_lock:
        return _pending
