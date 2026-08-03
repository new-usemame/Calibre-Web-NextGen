# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reader progress (bookmark) endpoints for /api/v1.

Reads/writes the SAME ub.Bookmark row the legacy reader uses
(/ajax/bookmark/<id>/<format>), with the SAME lowercase format key — so reading
progress is shared between the legacy reader and the SPA reader: open a book in
one, resume in the other. The bookmark_key is the epub.js CFI string.
"""
from flask import jsonify, request
from sqlalchemy import and_
from sqlalchemy.orm.attributes import flag_modified

from . import api_v1
from .. import logger, ub
from ..cw_login import current_user
from ..services import reading_position
from ..usermanagement import login_required_if_no_ano
from ..reader_settings import merged_reader_settings, resolved_reader_settings

log = logger.create()


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def _require_real_user():
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    return None


def _bookmark_filter(book_id, fmt):
    return and_(
        ub.Bookmark.user_id == int(current_user.id),
        ub.Bookmark.book_id == book_id,
        ub.Bookmark.format == fmt,
    )


@api_v1.route("/books/<int:book_id>/bookmark")
@login_required_if_no_ano
def get_bookmark(book_id):
    """Return the saved reading position (epub.js CFI) for this user/book/format."""
    guard = _require_real_user()
    if guard:
        return guard
    fmt = (request.args.get("format") or "epub").lower()
    row = ub.session.query(ub.Bookmark).filter(_bookmark_filter(book_id, fmt)).first()
    return jsonify({"bookmark": row.bookmark_key if row else None})


@api_v1.route("/books/<int:book_id>/bookmark", methods=["POST"])
@login_required_if_no_ano
def save_bookmark(book_id):
    """Persist (or, with an empty bookmark, clear) the reading position. Mirrors
    the legacy set_bookmark write so the two readers share one row."""
    guard = _require_real_user()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    fmt = (data.get("format") or "epub").lower()
    bookmark_key = data.get("bookmark") or ""

    # Replace-on-write: one bookmark per (user, book, format), like the legacy route.
    ub.session.query(ub.Bookmark).filter(_bookmark_filter(book_id, fmt)).delete()
    if bookmark_key:
        ub.session.merge(ub.Bookmark(
            user_id=current_user.id,
            book_id=book_id,
            format=fmt,
            bookmark_key=bookmark_key,
        ))
        # #1318: settle the required write before the optional one, so a bookmark
        # failure is not reported in the vocabulary of a progress-sharing failure
        # (and so the savepoint below cannot roll the bookmark back with it).
        if not ub.session_flush():
            return "", 500

        # #324: share the portable half of the position (the percentage) with the
        # user's other devices. Mirrors the legacy route so both readers behave
        # the same. Only on a save — an empty bookmark is a clear.
        percentage = reading_position.coerce_percentage(data.get("percentage"))
        if percentage is not None:
            try:
                reading_position.record_web_reader_progress(current_user, book_id, percentage)
            except Exception as e:
                # Position sharing must never cost the user their bookmark.
                log.warning("Could not share web reader progress for book %s: %s", book_id, e)

    # The SPA debounces one of these every 800ms; answering 204 on a rolled-back
    # write drops the position silently and tells the client not to retry.
    if not ub.session_commit("Bookmark for user {} in book {} via api".format(current_user.id, book_id)):
        return "", 500
    return "", 204


@api_v1.route("/reader/settings")
@login_required_if_no_ano
def get_reader_settings():
    """Return the complete per-user appearance contract shared by both readers."""
    guard = _require_real_user()
    if guard:
        return guard
    current = (getattr(current_user, "view_settings", None) or {}).get("reader", {})
    return jsonify({"reader": resolved_reader_settings(current)})


@api_v1.route("/reader/settings", methods=["POST"])
@login_required_if_no_ano
def save_reader_settings():
    """Merge a partial reader appearance update into User.view_settings."""
    guard = _require_real_user()
    if guard:
        return guard
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _err("invalid_settings", "Reader settings must be an object", 400)
    view_settings = dict(getattr(current_user, "view_settings", None) or {})
    merged = merged_reader_settings(view_settings.get("reader", {}), payload)
    view_settings["reader"] = merged
    current_user.view_settings = view_settings
    flag_modified(current_user, "view_settings")
    try:
        ub.session.commit()
    except Exception:
        ub.session.rollback()
        return _err("save_failed", "Could not save reader settings", 500)
    return jsonify({"reader": resolved_reader_settings(merged)})
