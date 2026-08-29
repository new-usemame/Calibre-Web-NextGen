# -*- coding: utf-8 -*-
"""Bounded, idempotent KEPUB production for books already delivered to Kobo."""

import os
import threading

from flask_babel import lazy_gettext as N_
from sqlalchemy.exc import SQLAlchemyError

from .. import config, db, logger, ub
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
# A broken metadata database/session should stop this hidden startup task after
# a handful of bounded recovery attempts, not turn a large synced library into
# one error (and traceback) per book. Three attempts tolerate a transient
# teardown race while putting a hard ceiling on the resulting log volume.
_SESSION_REBUILD_LIMIT = 3


class _DatabaseFailure(Exception):
    """A failure while traversing the Calibre metadata database."""

    def __init__(self, operation, error):
        self.operation = operation
        self.error = error
        super().__init__("{}: {}".format(operation, error))


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
        self.processed = 0

    def _status_message(self, total):
        return N_(
            u"%(processed)d/%(total)d processed: %(converted)d converted, "
            u"%(skipped)d skipped, %(failed)d failed",
            processed=self.processed,
            total=total,
            converted=self.converted,
            skipped=self.skipped,
            failed=self.failed,
        )

    @staticmethod
    def _discard_database(local_db, rollback=False):
        """End this worker greenlet's transaction and remove its scoped Session.

        Every ``CalibreDB`` made by the worker resolves through the same
        greenlet-scoped registry. Calling ``Session.close()`` is therefore not
        enough: the closed (or failed) Session remains registered and the next
        ``CalibreDB`` object receives it again. Assigning ``None`` uses
        ``CalibreDB.session``'s removal contract and makes the next access
        materialise a genuinely new Session.
        """
        if local_db is None:
            return
        try:
            session = local_db.session
        except Exception:
            session = None
        if rollback and session is not None:
            try:
                session.rollback()
            except Exception as error:
                log.warning("KEPUB backfill session rollback failed: %s", error)
        try:
            local_db.session = None
        except Exception as error:
            # Test doubles and downstream CalibreDB-compatible implementations
            # may not expose the removal setter. Close is the best available
            # fallback, while the production class always takes the path above.
            try:
                if session is not None:
                    session.close()
            except Exception:
                pass
            log.warning("KEPUB backfill could not discard its database session: %s", error)

    def _rebuild_database(self, failed_db, book_id):
        """Rollback/remove a poisoned Session and return a fresh CalibreDB.

        Recovery itself is circuit-broken. If the registry or engine cannot
        produce a usable Session, retrying once for every remaining book would
        reproduce the issue's unbounded log flood without doing useful work.
        """
        self._discard_database(failed_db, rollback=True)
        for attempt in range(1, _SESSION_REBUILD_LIMIT + 1):
            rebuilt_db = None
            try:
                rebuilt_db = db.CalibreDB(expire_on_commit=False, init=True)
                # Connectivity is insufficient: SQLite happily answers
                # ``SELECT 1`` when the engine is attached to an empty/wrong
                # database. Exercise the same Books and Data ORM paths used by
                # the task so a rebuilt session is not declared healthy until
                # the real Calibre metadata schema is available.
                self._probe_metadata_database(rebuilt_db, book_id)
                log.info(
                    "KEPUB backfill rebuilt its database session after book %s",
                    book_id,
                )
                return rebuilt_db
            except Exception as error:
                self._discard_database(rebuilt_db, rollback=True)
                log.warning(
                    "KEPUB backfill database session rebuild %d/%d failed "
                    "after book %s: %s",
                    attempt, _SESSION_REBUILD_LIMIT, book_id, error,
                )

        return None

    @staticmethod
    def _probe_metadata_database(local_db, book_id):
        """Exercise the real Books/Data ORM path and leave a clean transaction."""
        session = local_db.session
        if session is None:
            raise RuntimeError("CalibreDB did not provide a database session")
        if getattr(session, "is_active", True) is False:
            raise RuntimeError("CalibreDB provided an inactive database session")
        local_db.get_book(book_id)
        local_db.get_book_format(book_id, "KEPUB")
        local_db.get_book_format(book_id, "EPUB")
        if hasattr(session, "rollback"):
            session.rollback()

    @staticmethod
    def _book_metadata(local_db, book_id):
        """Return the book/formats, classifying failures by operation phase."""
        try:
            book = local_db.get_book(book_id)
            if not book:
                return None, None, None
            kepub = local_db.get_book_format(book_id, "KEPUB")
            epub = local_db.get_book_format(book_id, "EPUB")
            return book, kepub, epub
        except Exception as error:
            raise _DatabaseFailure("Calibre metadata lookup", error) from error

    @staticmethod
    def _persist_completion(completed):
        """Persist the rollback-compatibility marker with safe in-memory state."""
        config.config_kobo_kepub_backfill_completed = completed
        try:
            config.save()
        except Exception:
            # A failed write must never leave this process claiming completion.
            config.config_kobo_kepub_backfill_completed = False
            raise

    def _finish_failure(self, error):
        """Publish a failed terminal state only after clearing completion."""
        self._persist_completion(False)
        self._handleError(error)

    def run(self, worker_thread):
        global _pending
        try:
            if config.config_use_google_drive:
                log.info("KEPUB backfill skipped: Google Drive libraries are not supported")
                self._handleSuccess()
                return
            if not config.config_kepubifypath:
                self._finish_failure(N_(u"Kepubify is not configured"))
                return

            app_session = ub.get_new_session_instance()
            try:
                book_ids = [row[0] for row in app_session.query(ub.KoboSyncedBooks.book_id).distinct().all()]
            finally:
                app_session.close()

            local_db = db.CalibreDB(expire_on_commit=False, init=True)
            abort_reason = None
            consecutive_database_failures = 0
            try:
                total = len(book_ids)
                for index, book_id in enumerate(book_ids):
                    if self.stat == STAT_ENDED:
                        self.message = self._status_message(total)
                        self._persist_completion(False)
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
                    except _DatabaseFailure as error:
                        self.failed += 1
                        consecutive_database_failures += 1
                        log.error_or_exception(
                            "KEPUB backfill database failure for book {} "
                            "({}/{} consecutive): {}".format(
                                book_id,
                                consecutive_database_failures,
                                _SESSION_REBUILD_LIMIT,
                                error,
                            ))
                        if consecutive_database_failures >= _SESSION_REBUILD_LIMIT:
                            self._discard_database(local_db, rollback=True)
                            local_db = None
                            abort_reason = N_(
                                u"%(count)d consecutive Calibre metadata database failures",
                                count=_SESSION_REBUILD_LIMIT,
                            )
                        else:
                            local_db = self._rebuild_database(local_db, book_id)
                            if local_db is None:
                                abort_reason = N_(
                                    u"%(count)d consecutive Calibre metadata probe failures",
                                    count=_SESSION_REBUILD_LIMIT,
                                )
                    except Exception as error:
                        # Archive/layout/converter failures are isolated to the
                        # book. They prove the metadata path completed and must
                        # neither rebuild the Session nor advance its breaker.
                        consecutive_database_failures = 0
                        self.failed += 1
                        log.error_or_exception(
                            "KEPUB backfill failed for book {}: {}".format(book_id, error))
                    else:
                        # Only a real metadata lookup for a work-set book clears
                        # the post-rebuild database failure streak. A successful
                        # constructor/probe deliberately does not.
                        consecutive_database_failures = 0
                    finally:
                        self.processed += 1
                        self.progress = (index + 1) / total if total else 1
                    if abort_reason is not None:
                        break
            finally:
                # Remove, rather than merely close, this persistent worker
                # greenlet's scoped Session so later tasks cannot inherit it.
                self._discard_database(local_db)

            self.message = self._status_message(total)

            if abort_reason is not None:
                terminal_error = N_(
                    u"KEPUB backfill aborted after %(reason)s; %(status)s",
                    reason=abort_reason,
                    status=self.message,
                )
                log.error("%s", terminal_error)
                self._finish_failure(terminal_error)
                return

            # The legacy completion flag no longer gates startup: every eligible
            # boot performs the cheap idempotent scan. Keep writing it for safe
            # rollback to an older release, but never let it contradict a failed
            # terminal worker status.
            if self.failed:
                self._finish_failure(N_(
                    u"KEPUB backfill finished with failures; %(status)s",
                    status=self.message,
                ))
                return
            self._persist_completion(True)
            self._handleSuccess()
        except Exception:
            # ``CalibreTask.start`` turns an escaping exception into STAT_FAIL.
            # Clear the persisted compatibility marker here as well so failures
            # before the per-book loop (or while saving success) cannot retain a
            # stale True value from an earlier run.
            config.config_kobo_kepub_backfill_completed = False
            try:
                config.save()
            except Exception as error:
                log.error(
                    "KEPUB backfill could not persist its incomplete state: %s",
                    error,
                )
            raise
        finally:
            _clear_pending(self)

    def _backfill_one_book(self, local_db, book_id):
        """Convert one book, or record why it was skipped. Raises on I/O trouble."""
        book, kepub, epub = self._book_metadata(local_db, book_id)
        if not book:
            self.skipped += 1
            return
        if kepub or not epub:
            self.skipped += 1
            return
        if get_epub_layout(book, epub) == "pre-paginated":
            self.skipped += 1
            return

        file_path = os.path.join(config.get_book_path(), book.path, epub.name)
        settings = {"old_book_format": "EPUB", "new_book_format": "KEPUB"}
        conversion = TaskConvert(file_path, book_id, "EPUB -> KEPUB", settings, None)
        try:
            converted = conversion._convert_ebook_format()
        except SQLAlchemyError as error:
            raise _DatabaseFailure("KEPUB conversion metadata update", error) from error
        if converted:
            self.converted += 1
        else:
            # TaskConvert handles several SQLAlchemy failures internally and
            # returns a normal conversion failure. Probing here separates a
            # genuinely bad archive/tool run from a conversion that merely
            # masked a dead metadata session.
            try:
                self._probe_metadata_database(local_db, book_id)
            except Exception as error:
                raise _DatabaseFailure(
                    "KEPUB conversion metadata verification", error) from error
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
    """Queue the cheap idempotent scan whenever KEPUB preference is available.

    KoboSyncedBooks rows are deleted during ordinary reconciliation and SQLite
    may reuse their INTEGER PRIMARY KEY values, so no max-id watermark can prove
    the work-set unchanged. The task already skips existing KEPUBs and missing
    EPUBs before conversion; scanning is the reliable and inexpensive gate.
    """
    return enqueue_kepub_backfill(hidden=True)


def is_kepub_backfill_pending():
    """Return whether the composite backfill is queued or running."""
    with _enqueue_lock:
        return _pending
