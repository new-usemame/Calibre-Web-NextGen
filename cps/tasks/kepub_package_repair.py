# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Versioned, resumable repair of already-converted KEPUB packages."""

import hashlib
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone

from flask_babel import lazy_gettext as N_

from .. import config, constants, db, helper, logger, ub
from ..services.kepub_package_normalizer import (
    PROBE_CLEAN,
    PROBE_NEEDS_NORMALIZATION,
    PROBE_RETRYABLE,
    PROBE_UNSUPPORTED,
    kepub_package_needs_normalization,
    normalize_kepub_package,
)
from ..services.user_notices import create_notice_event
from ..services.worker import (
    CalibreTask, STAT_CANCELLED, STAT_ENDED, STAT_FAIL, STAT_FINISH_SUCCESS,
    WorkerThread,
)


log = logger.create()
REPAIR_VERSION = 1
NOTICE_TYPE = "kepub-package-repair"
REPAIR_STATUS_DETECTED = "detected"
REPAIR_STATUS_FILE_REPAIRED = "file_repaired"
REPAIR_STATUS_METADATA_BUMPED = "metadata_bumped"
REPAIR_STATUS_COMPLETED = "completed"
REPAIR_STATUS_FAILED = "failed"
REPAIR_STATUS_UNSUPPORTED = "unsupported"
_ACTIVE_STATUSES = (
    REPAIR_STATUS_DETECTED, REPAIR_STATUS_FILE_REPAIRED,
    REPAIR_STATUS_METADATA_BUMPED,
)
_enqueue_lock = threading.RLock()
_pending = False
_pending_owner = None
_TERMINAL_STATS = (STAT_FAIL, STAT_FINISH_SUCCESS, STAT_ENDED, STAT_CANCELLED)


def _clear_pending(task):
    """Release the startup latch only for the task instance that owns it."""
    global _pending, _pending_owner
    with _enqueue_lock:
        if _pending_owner is not None and _pending_owner is not task:
            return
        _pending = False
        _pending_owner = None


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_identity(path):
    """Return a best-effort stat fingerprint for the unsupported skip cache.

    This is not proof of content identity. A complete stat collision can delay
    one re-probe until a future repair-version bump; that failure mode is chosen
    over hashing a package we may not safely read.
    """
    file_stat = os.stat(path)
    return file_stat.st_size, file_stat.st_mtime_ns, file_stat.st_ctime_ns


def _probe_status(result):
    """Accept the typed production probe plus legacy injected bool/None probes."""
    status = getattr(result, "status", None)
    if status is not None:
        return status, getattr(result, "error_message", None)
    if result is False:
        return PROBE_CLEAN, None
    if result is None:
        return PROBE_RETRYABLE, None
    return PROBE_NEEDS_NORMALIZATION, None


def _backup_original(path, book, occurrence_key, expected_sha256):
    """Copy and hash-verify an affected package before the first mutation."""
    directory = os.path.join(constants.CONFIG_DIR, "kepub-repair-backups", str(book.id))
    os.makedirs(directory, exist_ok=True)
    destination = os.path.join(directory, occurrence_key + ".kepub")
    temporary = destination + ".tmp"
    try:
        shutil.copy2(path, temporary)
        if _sha256(temporary) != expected_sha256:
            raise OSError("KEPUB repair backup hash does not match its source")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def _audience_for_book(app_session, book_id):
    return [row[0] for row in app_session.query(ub.KoboSyncedBooks.user_id).filter(
        ub.KoboSyncedBooks.book_id == book_id,
    ).distinct().all()]


def _finish_notice(app_session, repair, book):
    audience = _audience_for_book(app_session, book.id)
    event = create_notice_event(
        app_session,
        notice_type=NOTICE_TYPE,
        occurrence_key=repair.occurrence_key,
        scope="book",
        audience_user_ids=audience,
        book_id=book.id,
        book_uuid=getattr(book, "uuid", None),
        title_snapshot=getattr(book, "title", None),
        payload={
            "message_key": "kepub_package_repaired",
            "repair_version": REPAIR_VERSION,
        },
    )
    repair.notice_event_id = event.id if event is not None else None
    repair.status = REPAIR_STATUS_COMPLETED
    app_session.commit()


def process_kepub_candidate(*, app_session, book, data, path, normalize,
                            mark_modified, commit_metadata, inspect_package=None,
                            backup_original=None, repair_version=None):
    """Repair or resume one candidate; dependencies are explicit for crash tests."""
    repair_version = REPAIR_VERSION if repair_version is None else repair_version
    repair = app_session.query(ub.KepubPackageRepair).filter(
        ub.KepubPackageRepair.book_id == book.id,
        ub.KepubPackageRepair.status.in_(_ACTIVE_STATUSES),
    ).order_by(ub.KepubPackageRepair.id.desc()).first()

    if repair is None:
        if inspect_package is not None:
            identity_before = _file_identity(path)
            unsupported = app_session.query(ub.KepubPackageRepair).filter(
                ub.KepubPackageRepair.book_id == book.id,
                ub.KepubPackageRepair.status == REPAIR_STATUS_UNSUPPORTED,
            ).order_by(ub.KepubPackageRepair.id.desc()).first()
            if unsupported is not None:
                recorded_identity = (
                    unsupported.source_size,
                    unsupported.source_mtime_ns,
                    unsupported.source_ctime_ns,
                )
                if (unsupported.repair_version == repair_version
                        and recorded_identity == identity_before):
                    return "unsupported"
                # A changed file or newer repair algorithm voids the old
                # disposition. Remove it so reverting an identity cannot revive
                # a row that has already been explicitly invalidated.
                app_session.delete(unsupported)
                app_session.commit()

            inspection = inspect_package(path)
            probe_status, probe_error = _probe_status(inspection)
            if probe_status == PROBE_CLEAN:
                return "clean"
            if probe_status == PROBE_RETRYABLE:
                return "failed"
            if probe_status == PROBE_UNSUPPORTED:
                identity_after = _file_identity(path)
                if identity_after != identity_before:
                    log.warning(
                        "KEPUB package changed while it was inspected; will retry: %s", path)
                    return "failed"
                unsupported = ub.KepubPackageRepair(
                    occurrence_key=str(uuid.uuid4()),
                    book_id=book.id,
                    book_uuid=getattr(book, "uuid", None),
                    # Unsupported candidates must never be content-hashed. The
                    # legacy NOT NULL column therefore carries an empty marker;
                    # stat fields hold the best-effort skip-cache fingerprint.
                    source_sha256="",
                    source_size=identity_after[0],
                    source_mtime_ns=identity_after[1],
                    source_ctime_ns=identity_after[2],
                    repair_version=repair_version,
                    status=REPAIR_STATUS_UNSUPPORTED,
                    error_message=probe_error,
                )
                app_session.add(unsupported)
                app_session.commit()
                return "unsupported"
            repair = ub.KepubPackageRepair(
                occurrence_key=str(uuid.uuid4()), book_id=book.id,
                book_uuid=getattr(book, "uuid", None), source_sha256=_sha256(path),
                repair_version=repair_version,
                status=REPAIR_STATUS_DETECTED,
            )
            app_session.add(repair)
            app_session.commit()

            if backup_original is not None:
                repair.backup_path = backup_original(
                    path, book, repair.occurrence_key, repair.source_sha256,
                )
                app_session.commit()

        changed = normalize(path)
        if changed is False:
            if repair is not None:
                repair.status = REPAIR_STATUS_FILE_REPAIRED
                repair.repaired_sha256 = _sha256(path)
                repair.file_repaired_at = datetime.now(timezone.utc)
                app_session.commit()
            else:
                return "clean"
        elif changed is None:
            if repair is not None:
                repair.status = REPAIR_STATUS_FAILED
                repair.error_message = "Package normalization failed; original preserved"
                app_session.commit()
            return "failed"
        else:
            if repair is None:  # Test/injected callers without a separate probe.
                repair = ub.KepubPackageRepair(
                    occurrence_key=str(uuid.uuid4()), book_id=book.id,
                    book_uuid=getattr(book, "uuid", None), source_sha256="unknown",
                    repair_version=repair_version,
                    status=REPAIR_STATUS_DETECTED,
                )
                app_session.add(repair)
            repair.status = REPAIR_STATUS_FILE_REPAIRED
            repair.repaired_sha256 = _sha256(path)
            repair.file_repaired_at = datetime.now(timezone.utc)
            app_session.commit()

    if repair.status == REPAIR_STATUS_DETECTED:
        if backup_original is not None and not repair.backup_path:
            repair.backup_path = backup_original(
                path, book, repair.occurrence_key, repair.source_sha256,
            )
            app_session.commit()
        changed = normalize(path)
        if changed is None:
            repair.status = REPAIR_STATUS_FAILED
            repair.error_message = "Package normalization failed; original preserved"
            app_session.commit()
            return "failed"
        repair.status = REPAIR_STATUS_FILE_REPAIRED
        repair.repaired_sha256 = _sha256(path)
        repair.file_repaired_at = datetime.now(timezone.utc)
        app_session.commit()

    if repair.status == REPAIR_STATUS_FILE_REPAIRED:
        data.uncompressed_size = os.path.getsize(path)
        mark_modified(book)
        commit_metadata()
        repair.status = REPAIR_STATUS_METADATA_BUMPED
        repair.metadata_bumped_at = datetime.now(timezone.utc)
        app_session.commit()

    if repair.status == REPAIR_STATUS_METADATA_BUMPED:
        _finish_notice(app_session, repair, book)
    return "repaired"


class TaskKepubPackageRepair(CalibreTask):
    def __init__(self):
        super().__init__(N_(u"Repair existing KEPUB packages"))
        self.clean = 0
        self.repaired = 0
        self.unsupported = 0
        self.failed = 0

    @property
    def name(self):
        return "KEPUB package repair"

    @property
    def is_cancellable(self):
        return True

    @CalibreTask.stat.setter
    def stat(self, value):
        CalibreTask.stat.fset(self, value)
        if value in _TERMINAL_STATS:
            _clear_pending(self)

    def run(self, worker_thread):
        try:
            if config.config_use_google_drive:
                log.warning("KEPUB package repair is unsupported for Google Drive libraries")
                self._handleError(N_(u"Google Drive KEPUB repair is not supported"))
                return
            app_session = ub.get_new_session_instance()
            local_db = db.CalibreDB(expire_on_commit=False, init=True)
            try:
                # Materialize only primary keys, then release the discovery
                # cursor/connection before any per-book metadata commit. This
                # avoids holding a metadata.db read cursor across the full scan.
                candidate_ids = [row[0] for row in local_db.session.query(
                    db.Data.id,
                ).filter(db.Data.format == "KEPUB").order_by(db.Data.id).all()]
                local_db.session.close()
                total = len(candidate_ids)
                for index, data_id in enumerate(candidate_ids):
                    if self.stat == STAT_ENDED:
                        return
                    try:
                        candidate = local_db.session.query(db.Data, db.Books).join(
                            db.Books, db.Books.id == db.Data.book,
                        ).filter(
                            db.Data.id == data_id,
                            db.Data.format == "KEPUB",
                        ).one_or_none()
                        if candidate is None:
                            continue
                        data, book = candidate
                        path = os.path.join(
                            config.get_book_path(), book.path,
                            data.name + "." + data.format.lower(),
                        )
                        result = process_kepub_candidate(
                            app_session=app_session, book=book, data=data, path=path,
                            repair_version=REPAIR_VERSION,
                            inspect_package=kepub_package_needs_normalization,
                            normalize=normalize_kepub_package,
                            backup_original=_backup_original,
                            mark_modified=lambda candidate: helper.mark_book_modified(
                                candidate, set_dirty=False, unsync=False),
                            commit_metadata=local_db.session.commit,
                        )
                        if result == "clean":
                            self.clean += 1
                        elif result == "repaired":
                            self.repaired += 1
                        elif result == "unsupported":
                            self.unsupported += 1
                        else:
                            self.failed += 1
                    except Exception as error:
                        local_db.session.rollback()
                        app_session.rollback()
                        self.failed += 1
                        log.error_or_exception(
                            "KEPUB package repair failed for data row %s: %s", data_id, error)
                    finally:
                        # Session.close() releases the metadata.db connection and
                        # clears ORM state; the scoped session is safely reusable
                        # for the next primary key.
                        local_db.session.close()
                        self.progress = (index + 1) / total if total else 1
            finally:
                app_session.close()
                local_db.session.close()

            self.message = N_(
                u"%(clean)d clean, %(repaired)d repaired, "
                u"%(unsupported)d unsupported, %(failed)d failed",
                clean=self.clean,
                repaired=self.repaired,
                unsupported=self.unsupported,
                failed=self.failed,
            )
            if self.failed:
                self._handleError(N_(u"%(count)d KEPUB repair(s) failed", count=self.failed))
            else:
                previous_version = config.config_kobo_kepub_package_repair_version
                config.config_kobo_kepub_package_repair_version = REPAIR_VERSION
                try:
                    config.save()
                except Exception:
                    config.config_kobo_kepub_package_repair_version = previous_version
                    raise
                if config.config_kobo_kepub_package_repair_version != REPAIR_VERSION:
                    self._handleError(N_(
                        u"KEPUB repair finished, but the completion marker could not "
                        u"be saved; the scan will run again"
                    ))
                else:
                    self._handleSuccess()
        finally:
            _clear_pending(self)


def enqueue_kepub_package_repair(user="System", hidden=False):
    global _pending, _pending_owner
    task = TaskKepubPackageRepair()
    with _enqueue_lock:
        if _pending:
            return False
        _pending = True
        _pending_owner = task
    try:
        WorkerThread.add(user, task, hidden=hidden)
    except Exception:
        _clear_pending(task)
        raise
    return True


def enqueue_startup_kepub_package_repair():
    if getattr(config, "config_kobo_kepub_package_repair_version", 0) < REPAIR_VERSION:
        return enqueue_kepub_package_repair(hidden=True)
    return False
