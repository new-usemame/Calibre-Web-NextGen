# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Administrator endpoints to restore covers whose ``has_cover`` flag is 0 (#2678).

Same two steps as the classic page: a scan that only reads and returns a signed
preview, then a restore that can only name books that scan listed.
"""
from __future__ import annotations

from flask import current_app, jsonify, request
from itsdangerous import BadSignature

from . import api_v1
from .. import calibre_db, config, db, helper, logger
from ..cw_login import current_user
from ..services import cover_flag_repair
from ..services.calibre_db_lock import metadata_db_write_lock
from ..usermanagement import login_required_if_no_ano

log = logger.create()


def _err(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


def _require_admin():
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not current_user.role_admin():
        return _err("forbidden", "Admin access required", 403)
    return None


def _google_drive_guard():
    if config.config_use_google_drive:
        return _err("not_available",
                    "This is not available for Google Drive libraries", 400)
    return None


@api_v1.route("/admin/cover-flags/scan", methods=["POST"])
@login_required_if_no_ano
def admin_cover_flags_scan():
    guard = _require_admin() or _google_drive_guard()
    if guard:
        return guard
    rows = cover_flag_repair.book_rows(calibre_db.session, db.Books)
    candidates = cover_flag_repair.find_candidates(rows, config.get_book_path())
    response = jsonify({
        "count": len(candidates),
        "books": [{"id": c.book_id, "title": c.title} for c in candidates],
        "token": (cover_flag_repair.make_token(current_app.config["SECRET_KEY"], candidates)
                  if candidates else None),
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@api_v1.route("/admin/cover-flags/restore", methods=["POST"])
@login_required_if_no_ano
def admin_cover_flags_restore():
    guard = _require_admin() or _google_drive_guard()
    if guard:
        return guard
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("book_ids"), list):
        return _err("invalid_request", "Send the preview token and the selected book ids", 400)
    try:
        preview_ids = cover_flag_repair.read_token(
            current_app.config["SECRET_KEY"], data.get("token") or "")
    except (BadSignature, TypeError, ValueError):
        return _err("invalid_token", "This preview is invalid or expired. Scan the library again.", 400)
    try:
        selected = cover_flag_repair.parse_selection(data["book_ids"], preview_ids)
    except cover_flag_repair.SelectionError as selection_error:
        if selection_error.code == "empty":
            return _err("empty_selection", "Select at least one book to restore.", 400)
        return _err("invalid_selection",
                    "The selected books do not match this preview. Scan the library again.", 400)
    try:
        outcome = cover_flag_repair.apply_selection(
            calibre_db.session, db.Books, selected, config.get_book_path(),
            write_lock=metadata_db_write_lock,
            on_fixed=helper.clear_cover_thumbnail_cache)
    except Exception as ex:
        log.error_or_exception(ex)
        return _err("write_failed",
                    "Could not update the library database. No covers were changed.", 500)
    log.info("Restored the cover flag on %d book(s); %d skipped",
             len(outcome.fixed), outcome.skipped)
    return jsonify({"fixed": len(outcome.fixed), "skipped": outcome.skipped})
