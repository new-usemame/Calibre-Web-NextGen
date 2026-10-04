# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Account-owned scores; writes never touch metadata.db."""
from datetime import datetime, timezone

from flask import jsonify, request
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from . import api_v1
from .. import calibre_db, config, logger, ub
from ..cw_login import current_user
from ..personal_ratings import ratings_for_books
from ..sort_orders import viewer_id
from ..usermanagement import login_required_if_no_ano

log = logger.create()


def _response(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['Vary'] = 'Cookie, Authorization'
    return response


def _error(code, message, status):
    return _response({'error': {'code': code, 'message': message}}, status)


@api_v1.route('/books/<int:book_id>/rating', methods=['GET', 'PUT', 'DELETE'])
@login_required_if_no_ano
def book_rating(book_id):
    uid = viewer_id(current_user)
    if uid is None:
        return _error('unauthorized', 'You must be signed in', 401)
    book = calibre_db.get_book_read_archived(
        book_id, config.config_read_column,
        allow_show_archived=True, allow_show_hidden=True,
        allow_show_global=bool(current_user.role_browse_global()),
        allow_public_shelf_books=True,
    )
    if not book:
        return _error('not_found', 'Book not found', 404)
    if request.method == 'GET':
        return _response(ratings_for_books(ub.session, uid, [book_id])[book_id])
    score = 0
    if request.method == 'PUT':
        body = request.get_json(silent=True)
        score = body.get('rating') if isinstance(body, dict) else None
        if type(score) is not int or not 0 <= score <= 10:
            return _error('invalid_rating', 'Rating must be an integer from 0 to 10', 400)
    now = datetime.now(timezone.utc)
    statement = sqlite_insert(ub.BookRating).values(
        user_id=uid, book_id=book_id, rating=score, updated_at=now,
    ).on_conflict_do_update(
        index_elements=[ub.BookRating.user_id, ub.BookRating.book_id],
        set_={'rating': score, 'updated_at': now},
    )
    try:
        ub.session.execute(statement)
        ub.session.commit()
    except SQLAlchemyError:
        ub.session.rollback()
        log.exception('Failed to save personal rating for user %s, book %s', uid, book_id)
        return _error('rating_save_failed', 'Could not save your rating.', 500)
    # Describe this committed write rather than racing a subsequent overwrite.
    return _response({'personal_rating': score if score else None})
