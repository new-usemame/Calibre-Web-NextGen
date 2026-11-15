# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Account-owned scores; writes never touch metadata.db."""
from flask import jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from . import api_v1
from .. import logger, ub
from ..cw_login import current_user
from ..personal_ratings import may_rate, ratings_for_books, set_personal_rating
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
    if not may_rate(book_id, current_user):
        return _error('not_found', 'Book not found', 404)
    if request.method == 'GET':
        try:
            return _response(ratings_for_books(ub.session, uid, [book_id])[book_id])
        except SQLAlchemyError:
            ub.session.rollback()
            log.exception('Failed to load personal rating for user %s, book %s', uid, book_id)
            return _error('rating_load_failed', 'Could not load your rating.', 500)
    score = 0
    if request.method == 'PUT':
        body = request.get_json(silent=True)
        score = body.get('rating') if isinstance(body, dict) else None
        if type(score) is not int or not 0 <= score <= 10:
            return _error('invalid_rating', 'Rating must be an integer from 0 to 10', 400)
    try:
        set_personal_rating(ub.session, uid, book_id, score)
        saved = ub.session_commit() is not False
    except SQLAlchemyError:
        ub.session.rollback()
        log.exception('Failed to save personal rating for user %s, book %s', uid, book_id)
        return _error('rating_save_failed', 'Could not save your rating.', 500)
    if not saved:
        log.error('Failed to save personal rating for user %s, book %s', uid, book_id)
        return _error('rating_save_failed', 'Could not save your rating.', 500)
    # Describe this committed write rather than racing a subsequent overwrite.
    return _response({'personal_rating': score if score else None})
