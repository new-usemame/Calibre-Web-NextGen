# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Duplicate-books endpoint for /api/v1 (admin/edit).

Serializes the same duplicate groups the legacy /duplicates page renders
(cps/duplicate_index.get_duplicate_groups_from_index) as JSON so the SPA can
show them natively. Dismiss/undismiss reuse the existing legacy JSON routes
(/duplicates/dismiss/<hash>) — no logic duplicated. The manual full-scan
trigger (#1048) delegates to the legacy /duplicates/trigger-scan view for the
same reason: queueing, cache invalidation and the synchronous fallback stay a
single implementation.
"""
from flask import jsonify

from . import api_v1
from .. import logger
from ..cw_login import current_user
from ..usermanagement import login_required_if_no_ano

log = logger.create()


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def _require_admin_or_edit():
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not (current_user.role_admin() or current_user.role_edit()):
        return _err("forbidden", "Admin or edit permission required", 403)
    return None


@api_v1.route("/duplicates")
@login_required_if_no_ano
def list_duplicates():
    guard = _require_admin_or_edit()
    if guard:
        return guard

    needs_scan = False
    groups = []
    try:
        from scripts.cwa_db import CWA_DB
        from ..duplicate_index import (
            get_duplicate_groups_from_index,
            duplicate_index_needs_manual_full_scan,
            library_has_books,
        )
        settings = CWA_DB().cwa_settings
        needs_scan = bool(library_has_books() and duplicate_index_needs_manual_full_scan(settings))
        if not needs_scan:
            groups = get_duplicate_groups_from_index(
                settings, include_dismissed=False,
                user_id=current_user.id if current_user else None,
            )
    except Exception:
        log.error("Could not load duplicate groups", exc_info=True)

    items = []
    for g in groups:
        items.append({
            "group_hash": g.get("group_hash"),
            "title": g.get("title"),
            "author": g.get("author"),
            "count": g.get("count"),
            "books": [{
                "id": b.id,
                "title": b.title,
                "authors": (getattr(b, "author_names", "") or "").replace("|", ","),
                "formats": [d.format for d in (getattr(b, "data", None) or [])],
                "cover_url": ("/cover/%d/sm" % b.id) if getattr(b, "has_cover", 0) else None,
            } for b in g.get("books", [])],
        })
    return jsonify({"items": items, "needs_scan": needs_scan})


@api_v1.route("/duplicates/scan", methods=["POST"])
@login_required_if_no_ano
def trigger_duplicate_scan():
    """Queue a manual full duplicate scan (#1048).

    Before this existed the SPA had no way to run a scan at all: the classic
    "Run Full Duplicate Scan" button lives on the legacy /duplicates page, which
    the SPA route shadows, and the SPA's needs-scan empty state pointed admins at
    the CWA settings page, which has no such control.

    Unlike the legacy route this is NOT csrf-exempt — the SPA sends X-CSRFToken
    on every POST, so the endpoint keeps the standard /api/v1 protection.

    Two guards the legacy route does not have, because a button in the SPA is far
    easier to hammer than the classic page's (it re-enables as soon as the POST
    returns, long before the scan finishes):

      * single-flight — a full scan already queued or running short-circuits to an
        idempotent 200 instead of stacking another full-library scan behind it;
      * no synchronous fallback — the legacy path rebuilds the index inline when
        the worker cannot take the task, and CWNG runs gevent WITHOUT
        monkey.patch_all(), so that would freeze every other request for the
        length of a full scan. Here that failure returns 503 and the user can
        retry.
    """
    guard = _require_admin_or_edit()
    if guard:
        return guard

    from ..cwa_functions import _duplicate_full_scan_running
    if _duplicate_full_scan_running():
        return jsonify({
            "success": True, "queued": False, "already_running": True,
            "message": "A full duplicate scan is already running.",
        })

    from ..duplicates import trigger_scan as legacy_trigger_scan
    return legacy_trigger_scan(allow_sync_fallback=False)
