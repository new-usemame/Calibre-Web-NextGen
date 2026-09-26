# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Background Hardcover get-or-add sync for shelf additions (fork #381).

Adding a book to a Kobo-synced shelf mirrors it to the user's Hardcover
library ("Want to Read"). The single-add route used to do this inline —
blocking the HTTP response on up to two external API calls — and all three
bulk-add routes skipped it entirely, so the same user intent produced a
different outcome depending on which button was pressed.

Every add path now queues this task instead. It runs on the WorkerThread,
shows up in the Tasks list, tolerates per-book failures, and is cancellable
between books. The token and book ids are captured at enqueue time because
the worker thread has no request context (no ``current_user``).
"""

import threading
import time

from cps import db, logger
from cps.services import hardcover
from cps.services.worker import CalibreTask, STAT_CANCELLED, STAT_ENDED, WorkerThread
from flask_babel import lazy_gettext as N_

# Pause between books so a big series add doesn't burst-hammer the API.
INTER_BOOK_DELAY = 0.2


class TaskHardcoverBulkSync(CalibreTask):
    """Get-or-add each book on Hardcover, mirroring the single-add semantics:
    a book already in the user's Hardcover library is left alone (status
    updates belong to update_reading_progress); otherwise it is added.
    Books without ``hardcover-*`` identifiers are skipped without an API
    call — Hardcover can't match them and would just log two warnings."""

    def __init__(self, token, book_ids, shelf_name,
                 task_message=N_('Syncing shelf additions to Hardcover')):
        super(TaskHardcoverBulkSync, self).__init__(task_message)
        self.log = logger.create()
        self.token = token
        self.book_ids = list(book_ids or [])
        self.shelf_name = shelf_name
        self.synced = 0
        self.already_synced = 0
        self.skipped_no_identifiers = 0
        self.errors = 0

    def _cancelled(self):
        return self.stat in (STAT_CANCELLED, STAT_ENDED)

    def run(self, worker_thread):
        if hardcover is None:
            self._handleError("Hardcover service is not available")
            return
        if not self.book_ids:
            self._handleSuccess()
            return
        try:
            client = hardcover.HardcoverClient(self.token)
        except hardcover.MissingHardcoverToken:
            self._handleError("No valid Hardcover token configured for this user")
            return
        except Exception as ex:
            self._handleError("Could not connect to Hardcover: {}".format(ex))
            return

        calibre_db = db.CalibreDB(expire_on_commit=False, init=True)
        try:
            total = len(self.book_ids)
            for position, book_id in enumerate(self.book_ids):
                if self._cancelled():
                    self.log.info("Hardcover shelf sync cancelled by user")
                    return
                try:
                    book = calibre_db.session.query(db.Books).filter(
                        db.Books.id == book_id).one_or_none()
                    if book is None:
                        # Deleted between enqueue and run — nothing to sync.
                        self.skipped_no_identifiers += 1
                        continue
                    identifiers = {ident.type: ident.val for ident in book.identifiers
                                   if "hardcover" in ident.type}
                    if not identifiers:
                        self.skipped_no_identifiers += 1
                        continue
                    self._sync_book(client, identifiers)
                except Exception as ex:
                    # One bad book must not strand the rest of the batch.
                    self.errors += 1
                    self.log.error("Hardcover sync failed for book %s: %s", book_id, ex)
                finally:
                    self.progress = (position + 1) / total
                if position + 1 < total and INTER_BOOK_DELAY:
                    time.sleep(INTER_BOOK_DELAY)

            summary = self._summary()
            self.log.info(summary)
            if self.errors and not (self.synced or self.already_synced):
                self._handleError(summary)
            else:
                self.message = summary
                self._handleSuccess()
        finally:
            calibre_db.session.close()

    def _sync_book(self, client, identifiers):
        if client.get_user_book(identifiers):
            self.already_synced += 1
        else:
            client.add_book(identifiers)
            self.synced += 1

    def _summary(self):
        return ("Hardcover sync for shelf '{}': {} added, {} already synced, "
                "{} without Hardcover identifiers, {} errors").format(
            self.shelf_name, self.synced, self.already_synced,
            self.skipped_no_identifiers, self.errors)

    @property
    def name(self):
        return N_("Hardcover Sync")

    def __str__(self):
        return "Hardcover sync for shelf '{}' ({} books)".format(
            self.shelf_name, len(self.book_ids))

    @property
    def is_cancellable(self):
        return True


class TaskHardcoverMarkRead(TaskHardcoverBulkSync):
    """Mirror a manual "mark as read" to the user's Hardcover library (#2289).

    Marking a book read in the web UI, the new UI, bulk edit or the KOReader
    library plugin used to reach Kobo and KOReader but never Hardcover, so a
    user with Hardcover sync on saw nothing there. Marking unread deliberately
    sends nothing: it would have to guess what to delete from their Hardcover
    history. Same worker, identifier skip and per-book tolerance as the shelf
    sync; only the per-book action differs.
    """

    def __init__(self, token, book_ids,
                 task_message=N_('Marking books read on Hardcover')):
        super(TaskHardcoverMarkRead, self).__init__(
            token, book_ids, None, task_message=task_message)

    def _sync_book(self, client, identifiers):
        if client.mark_book_read(identifiers):
            self.synced += 1
        else:
            self.errors += 1

    def _summary(self):
        return ("Hardcover mark-read: {} marked read, "
                "{} without Hardcover identifiers, {} errors").format(
            self.synced, self.skipped_no_identifiers, self.errors)

    def __str__(self):
        return "Hardcover mark-read ({} books)".format(len(self.book_ids))


# Web-reader positions waiting to reach Hardcover, keyed (user_id, book_id) ->
# (token, percentage). A key is present exactly while its task is queued.
_pending_progress = {}
_pending_lock = threading.Lock()


def queue_reading_progress(owner, token, user_id, book_id, percentage):
    """Queue a web-reader position for Hardcover, coalescing per book (#2289).

    The web reader saves on every page turn, so one task per save would queue
    a Hardcover round trip per page. While a push for this book is waiting,
    a later save only replaces the position it will send. Returns True when a
    new task was queued.
    """
    key = (user_id, book_id)
    with _pending_lock:
        coalesced = key in _pending_progress
        _pending_progress[key] = (token, percentage)
    if coalesced:
        return False
    try:
        WorkerThread.add(owner, TaskHardcoverReadingProgress(user_id, book_id),
                         hidden=True)
    except Exception:
        # No task will ever pop the key, and a stranded key would coalesce
        # every later save of this book into nothing.
        with _pending_lock:
            _pending_progress.pop(key, None)
        raise
    return True


class TaskHardcoverReadingProgress(CalibreTask):
    """Send the latest web-reader position for one book to Hardcover (#2289).

    Kobo and KOReader already push their progress; the web reader reached
    both devices but never Hardcover. Same client call, off the request path.
    """

    def __init__(self, user_id, book_id,
                 task_message=N_('Syncing reading progress to Hardcover')):
        super(TaskHardcoverReadingProgress, self).__init__(task_message)
        self.log = logger.create()
        self.user_id = user_id
        self.book_id = book_id

    def run(self, worker_thread):
        # Claim the position first: a save from now on queues a new task.
        with _pending_lock:
            pending = _pending_progress.pop((self.user_id, self.book_id), None)
        if pending is None or hardcover is None:
            self._handleSuccess()
            return
        token, percentage = pending
        try:
            identifiers = self._identifiers()
            if identifiers:
                hardcover.HardcoverClient(token).update_reading_progress(
                    identifiers, percentage)
        except Exception as ex:
            self.log.error("Hardcover progress sync failed for book %s: %s",
                           self.book_id, ex)
            self._handleError("Hardcover progress sync failed: {}".format(ex))
            return
        self._handleSuccess()

    def _identifiers(self):
        calibre_db = db.CalibreDB(expire_on_commit=False, init=True)
        try:
            book = calibre_db.session.query(db.Books).filter(
                db.Books.id == self.book_id).one_or_none()
            if book is None:
                return {}
            return {ident.type: ident.val for ident in book.identifiers
                    if "hardcover" in ident.type}
        finally:
            calibre_db.session.close()

    @property
    def name(self):
        return N_("Hardcover Sync")

    def __str__(self):
        return "Hardcover reading progress (book {})".format(self.book_id)

    @property
    def is_cancellable(self):
        return False
