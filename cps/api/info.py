# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Info endpoints for /api/v1: About/stats + the task queue.

Reuses the legacy cores (about.collect_stats, tasks_status.render_task_status,
WorkerThread) so the SPA shows exactly what the Jinja pages show.
"""
from flask import jsonify

from . import api_v1
from .. import calibre_db, db
from ..cw_login import current_user
from ..usermanagement import login_required_if_no_ano
from ..about import collect_stats
from ..tasks_status import render_task_status
from ..services.worker import WorkerThread


@api_v1.route("/about")
@login_required_if_no_ano
def about_info():
    """Library counts + component versions (the legacy Statistics page).

    Counts are for everyone; versions are admin-only (#1287). collect_stats()
    reports the host kernel build string, the Python build and every installed
    dependency version -- a fingerprint an attacker can match against known
    CVEs on a publicly reachable instance.

    The Jinja page has always gated that block on role_admin() (stats.html),
    but the SPA's API was written without the check, so on an instance with
    anonymous browsing enabled the whole map was one unauthenticated GET away.
    Gating here rather than in the client is what actually closes it: a UI
    conditional still ships the data over the wire.

    The key stays present-but-empty for non-admins so AboutInfo.versions holds
    its shape for every caller, and the client can treat "server sent versions"
    as the single source of truth for whether to render the section.
    """
    is_admin = current_user.role_admin()
    resp = jsonify({
        "counts": {
            "books": calibre_db.session.query(db.Books).count(),
            "authors": calibre_db.session.query(db.Authors).count(),
            "categories": calibre_db.session.query(db.Tags).count(),
            "series": calibre_db.session.query(db.Series).count(),
        },
        # collect_stats() returns an ordered {name: version} map. Not called at
        # all for a non-admin, so there is nothing to leak into a log or a
        # traceback on the way to being discarded.
        "versions": collect_stats() if is_admin else {},
    })
    # The body now depends on who asked, so a shared cache must never hand an
    # admin's copy to anyone else. Flask sets Vary: Cookie when the session is
    # touched, but reverse-proxy header login resolves the user from
    # g.flask_httpauth_user without touching it, so that is not guaranteed here.
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@api_v1.route("/tasks")
@login_required_if_no_ano
def tasks_list():
    """Worker queue. render_task_status already scopes rows to the caller (own
    tasks, or all for an admin) and localizes status text."""
    worker = WorkerThread.get_instance()
    return jsonify({"items": render_task_status(worker.tasks)})


@api_v1.route("/tasks/<task_id>/cancel", methods=["POST"])
@login_required_if_no_ano
def cancel_task_api(task_id):
    """Cancel a cancellable task. A non-admin may only cancel their own task —
    we resolve the task by id and check ownership before ending it (the legacy
    /ajax/canceltask did not scope this)."""
    worker = WorkerThread.get_instance()
    target = None
    for __, user, __, task, __ in worker.tasks:
        if str(task.id) == str(task_id):
            if user == current_user.name or current_user.role_admin():
                target = task
            else:
                return jsonify({"error": {"code": "forbidden",
                                          "message": "Not your task"}}), 403
            break
    if target is None:
        return jsonify({"error": {"code": "not_found", "message": "Task not found"}}), 404
    worker.end_task(target.id)
    return "", 204
