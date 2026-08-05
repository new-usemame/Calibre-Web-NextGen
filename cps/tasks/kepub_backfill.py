# -*- coding: utf-8 -*-
"""Bounded, idempotent KEPUB production for books already delivered to Kobo."""

import os
import threading

from flask_babel import lazy_gettext as N_

from .. import config, db, helper, logger, ub
from ..epub import get_epub_layout
from ..services.worker import CalibreTask, STAT_ENDED, WorkerThread
from .convert import TaskConvert

log = logger.create()
_enqueue_lock = threading.Lock()
_pending = False


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
                    book = local_db.get_book(book_id)
                    if not book:
                        self.skipped += 1
                        continue
                    kepub = local_db.get_book_format(book_id, "KEPUB")
                    epub = local_db.get_book_format(book_id, "EPUB")
                    if kepub or not epub:
                        self.skipped += 1
                        continue
                    if get_epub_layout(book, epub) == "pre-paginated":
                        self.skipped += 1
                        continue

                    file_path = os.path.join(config.get_book_path(), book.path, epub.name)
                    settings = {"old_book_format": "EPUB", "new_book_format": "KEPUB"}
                    conversion = TaskConvert(file_path, book_id, "EPUB -> KEPUB", settings, None)
                    if conversion._convert_ebook_format():
                        self.converted += 1
                    else:
                        self.failed += 1
                        log.error("KEPUB backfill failed for book %d: %s", book_id, conversion.error)
                    self.progress = (index + 1) / total if total else 1
            finally:
                local_db.session.close()

            if self.failed:
                self._handleError(N_(u"%(count)d KEPUB conversion(s) failed", count=self.failed))
                return
            config.config_kobo_kepub_backfill_completed = True
            config.save()
            self._handleSuccess()
        finally:
            with _enqueue_lock:
                _pending = False

    @property
    def name(self):
        return N_(u"Kobo KEPUB backfill")

    @property
    def is_cancellable(self):
        return True


def enqueue_kepub_backfill(user="System", hidden=False):
    """Queue at most one composite scan/conversion task at a time."""
    global _pending
    if (not config.config_kobo_prefer_kepub or not config.config_kepubifypath
            or config.config_use_google_drive):
        return False
    with _enqueue_lock:
        if _pending:
            return False
        _pending = True
    WorkerThread.add(user, TaskKepubBackfill(), hidden=hidden)
    return True


def enqueue_startup_kepub_backfill():
    if not config.config_kobo_kepub_backfill_completed:
        return enqueue_kepub_backfill(hidden=True)
    return False
