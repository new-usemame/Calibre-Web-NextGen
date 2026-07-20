# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Background annotation sync-target fan-out (fork #920 / #699).

Pushing a highlight to a sync target (today: Hardcover) is a blocking HTTPS
call with a 10s timeout. It used to run on the request greenlet, and because
CWNG serves with gevent WITHOUT ``monkey.patch_all()``, that blocking socket
froze the entire application for the length of the call — measured at 30s of
whole-app freeze for a three-highlight KOReader sync, including a 28s
anonymous ``GET /login``. Downstream, the KOReader plugin's 15s timeout
expired (reported as "Server push failed" on syncs the server had actually
saved) and the 3s Docker healthcheck missed enough probes to restart the
container.

The annotation itself is persisted and committed on the request path, so the
user's data is safe before this task ever runs; only the remote half is
deferred. Each target row is left ``pending`` until the worker reports back.

Jobs are coalesced per request so one KOReader sync produces one task rather
than one per highlight. The user id and job list are captured at enqueue time
because the worker thread has no request context.
"""

from flask_babel import lazy_gettext as N_

from cps import logger, ub
from cps.services import annotation_sync
from cps.services.worker import CalibreTask, WorkerThread


class TaskAnnotationSync(CalibreTask):
    """Run queued annotation push/delete jobs against the remote targets."""

    def __init__(self, user_id, jobs,
                 task_message=N_('Syncing annotations to reading services')):
        super(TaskAnnotationSync, self).__init__(task_message)
        self.log = logger.create()
        self.user_id = user_id
        self.jobs = list(jobs or [])

    def run(self, worker_thread):
        self.log.debug("annotation_sync task: starting %d job(s)", len(self.jobs))
        if not self.jobs:
            self._handleSuccess()
            return
        session = None
        calibre_db = None
        try:
            # Background tasks must not touch the global web-request ub.session.
            session = ub.init_db_thread()
            user = session.query(ub.User).filter(ub.User.id == self.user_id).first()
            if user is None:
                self._handleError("User {} no longer exists".format(self.user_id))
                return

            from cps import db
            calibre_db = db.CalibreDB(expire_on_commit=False, init=True)

            def _load_book(book_id):
                return (
                    calibre_db.session.query(db.Books)
                    .filter(db.Books.id == book_id)
                    .first()
                )

            annotation_sync.execute_jobs(session, user, self.jobs,
                                         book_loader=_load_book)
            session.commit()
            self.progress = 1
            self.message = "Synced {} annotation change(s)".format(len(self.jobs))
            self._handleSuccess()
        except Exception as ex:
            if session is not None:
                session.rollback()
            self.log.error("Annotation sync task failed: %s", ex)
            self._handleError("Annotation sync failed: {}".format(ex))
        finally:
            if calibre_db is not None:
                try:
                    calibre_db.session.close()
                except Exception:
                    pass
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass

    @property
    def name(self):
        return N_("Annotation Sync")

    def __str__(self):
        return "Annotation sync ({} job(s)) for user {}".format(
            len(self.jobs), self.user_id)

    @property
    def is_cancellable(self):
        return True


def _submit(user_name, user_id, jobs):
    logger.create().debug(
        "annotation_sync: queueing %d remote job(s) for user %s", len(jobs), user_id)
    WorkerThread.add(user_name, TaskAnnotationSync(user_id, jobs), hidden=True)


def enqueue_annotation_sync(user, jobs) -> None:
    """Queue remote annotation work for ``user``.

    Inside a request, jobs accumulate on ``g`` and are submitted once when the
    request finishes — a KOReader sync re-pushes the device's whole local set,
    so per-annotation submission would file a dozen tasks for one sync.
    """
    if not jobs:
        return
    user_id = getattr(user, "id", None)
    if user_id is None:
        return
    user_name = getattr(user, "name", None) or "System"

    try:
        from flask import g, has_request_context, after_this_request
        in_request = has_request_context()
    except Exception:  # pragma: no cover - flask always importable in app
        in_request = False

    if not in_request:
        _submit(user_name, user_id, jobs)
        return

    pending = getattr(g, "_annotation_sync_jobs", None)
    if pending is None:
        pending = {}
        g._annotation_sync_jobs = pending

        @after_this_request
        def _flush(response):
            queued = getattr(g, "_annotation_sync_jobs", None) or {}
            g._annotation_sync_jobs = None
            for (uid, uname), batch in queued.items():
                try:
                    _submit(uname, uid, batch)
                except Exception:
                    logger.create().exception(
                        "annotation_sync: could not queue background task")
            return response

    pending.setdefault((user_id, user_name), []).extend(jobs)
