# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Private per-user book reviews for /api/v1."""

from datetime import datetime, timezone

from flask import jsonify, request
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from . import api_v1
from .. import calibre_db, config, logger, ub
from ..cw_login import current_user
from ..usermanagement import login_required_if_no_ano

log = logger.create()
MAX_REVIEW_CHARS = 10000


def _private_json(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Cookie, Authorization"
    return response


def _error(code, message, status):
    return _private_json({"error": {"code": code, "message": message}}, status)


def _require_real_user():
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _error("unauthorized", "You must be signed in", 401)
    return None


def _can_browse_global():
    try:
        return bool(current_user.role_browse_global())
    except (AttributeError, RuntimeError):
        return False


def _visible_book(book_id):
    """Match book_detail visibility, including content policy and public shelves."""
    return calibre_db.get_book_read_archived(
        book_id, config.config_read_column,
        allow_show_archived=True, allow_show_hidden=True,
        allow_show_global=_can_browse_global(),
        allow_public_shelf_books=True,
    )


def _utc_iso(value):
    if value is None:
        value = datetime.now(timezone.utc)
    elif value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _review_for(book_id):
    return ub.session.query(ub.BookReview).filter(
        ub.BookReview.user_id == int(current_user.id),
        ub.BookReview.book_id == book_id,
    ).first()


def _check_access(book_id):
    guard = _require_real_user()
    if guard is not None:
        return guard
    if not _visible_book(book_id):
        return _error("not_found", "Book not found", 404)
    return None


@api_v1.route("/books/<int:book_id>/review", methods=["GET"])
@login_required_if_no_ano
def get_book_review(book_id):
    """Return this authenticated user's private note for a visible book."""
    denied = _check_access(book_id)
    if denied is not None:
        return denied
    row = _review_for(book_id)
    review = None if row is None else {
        "text": row.text,
        "updated_at": _utc_iso(row.updated_at),
    }
    return _private_json({"review": review})


@api_v1.route("/books/<int:book_id>/review", methods=["PUT"])
@login_required_if_no_ano
def put_book_review(book_id):
    """Create or replace a private review using an atomic SQLite upsert."""
    denied = _check_access(book_id)
    if denied is not None:
        return denied
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("text"), str):
        return _error("invalid_review", "Review text must be a string", 400)
    text = body["text"]
    if not text.strip():
        return _error("invalid_review", "Review text cannot be empty", 400)
    if len(text) > MAX_REVIEW_CHARS:
        return _error("review_too_long", "Review text exceeds 10000 characters", 400)

    now = datetime.now(timezone.utc)
    statement = sqlite_insert(ub.BookReview).values(
        user_id=int(current_user.id), book_id=book_id, text=text,
        created_at=now, updated_at=now,
    ).on_conflict_do_update(
        index_elements=[ub.BookReview.user_id, ub.BookReview.book_id],
        set_={"text": text, "updated_at": now},
    )
    try:
        ub.session.execute(statement)
        ub.session.commit()
    except SQLAlchemyError:
        ub.session.rollback()
        log.exception("Failed to save private review for user %s, book %s",
                      current_user.id, book_id)
        return _error("review_save_failed", "Could not save review", 500)
    return _private_json({"review": {
        # This response describes this committed PUT. A second request may
        # delete or replace the row immediately after our transaction; a
        # post-commit requery would make this response race-dependent.
        "text": text,
        "updated_at": _utc_iso(now),
    }})


@api_v1.route("/books/<int:book_id>/review", methods=["DELETE"])
@login_required_if_no_ano
def delete_book_review(book_id):
    """Delete this user's note; repeated deletion is harmless."""
    denied = _check_access(book_id)
    if denied is not None:
        return denied
    try:
        ub.session.query(ub.BookReview).filter(
            ub.BookReview.user_id == int(current_user.id),
            ub.BookReview.book_id == book_id,
        ).delete(synchronize_session=False)
        ub.session.commit()
    except SQLAlchemyError:
        ub.session.rollback()
        log.exception("Failed to delete private review for user %s, book %s",
                      current_user.id, book_id)
        return _error("review_delete_failed", "Could not delete review", 500)
    response = _private_json({}, 204)
    response.set_data(b"")
    return response
