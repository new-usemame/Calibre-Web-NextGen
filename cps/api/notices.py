# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Generic per-user notice inbox endpoints."""

from flask import jsonify, request

from . import api_v1
from .. import ub
from ..cw_login import current_user
from ..services.user_notices import dismiss_notice, dismiss_notices, list_active_notices, serialize_notice
from ..usermanagement import login_required_if_no_ano


def _response(events):
    response = jsonify({
        "notices": [serialize_notice(event) for event in events],
        "summary": {"count": len(events)},
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response


@api_v1.route("/notices")
@login_required_if_no_ano
def active_notices():
    raw_book_id = request.args.get("book_id")
    try:
        book_id = int(raw_book_id) if raw_book_id is not None else None
    except (TypeError, ValueError):
        return jsonify({"error": {
            "code": "invalid_book_id", "message": "book_id must be an integer",
        }}), 400
    notice_type = request.args.get("type")
    events = list_active_notices(
        ub.session, user_id=current_user.id, book_id=book_id,
        notice_type=notice_type or None, mark_presented=True,
    )
    return _response(events)


@api_v1.route("/notices/<int:notice_id>/dismiss", methods=["POST"])
@login_required_if_no_ano
def dismiss_one_notice(notice_id):
    changed = dismiss_notice(ub.session, user_id=current_user.id, notice_id=notice_id)
    if not changed:
        delivery = ub.session.query(ub.UserNoticeDelivery).filter_by(
            user_id=current_user.id, event_id=notice_id,
        ).one_or_none()
        if delivery is None:
            return jsonify({"error": {"code": "not_found", "message": "Notice not found"}}), 404
    remaining = len(list_active_notices(ub.session, user_id=current_user.id))
    return jsonify({"dismissed": changed, "remaining": remaining})


@api_v1.route("/notices/dismiss", methods=["POST"])
@login_required_if_no_ano
def dismiss_many_notices():
    payload = request.get_json(silent=True) or {}
    notice_ids = payload.get("notice_ids")
    if not isinstance(notice_ids, list) or not notice_ids or len(notice_ids) > 500:
        return jsonify({"error": {
            "code": "invalid_notice_ids",
            "message": "notice_ids must be a non-empty list of at most 500 ids",
        }}), 400
    try:
        normalized = [int(value) for value in notice_ids]
    except (TypeError, ValueError):
        return jsonify({"error": {
            "code": "invalid_notice_ids", "message": "Every notice id must be an integer",
        }}), 400
    changed = dismiss_notices(ub.session, user_id=current_user.id, notice_ids=normalized)
    remaining = len(list_active_notices(ub.session, user_id=current_user.id))
    return jsonify({"dismissed": changed, "remaining": remaining})
