# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-account scores and opt-in aggregate projections at the SQL seam."""
import json

from sqlalchemy import Text, case, cast, func, literal, select
from sqlalchemy.sql.functions import coalesce

from . import constants, db, ub


PERSONAL_RATING_SORTS = frozenset(('ratingdesc', 'ratingasc'))


def personal_score(user_id):
    """Correlated score for filtering/ordering *before* pagination; 0=unrated."""
    if user_id is None:
        return literal(0)
    return coalesce(select(ub.BookRating.rating).where(
        ub.BookRating.user_id == int(user_id),
        ub.BookRating.book_id == db.Books.id,
    ).correlate(db.Books).scalar_subquery(), 0)


def personal_rating_order(user_id, descending=True):
    score = personal_score(user_id)
    # Both directions keep unrated books last, then stable date/id ties.
    return [(score > 0).desc(), score.desc() if descending else score.asc(),
            db.Books.timestamp.desc(), db.Books.id.desc()]


def _sharing_consent():
    # Legacy settings may contain malformed JSON. Evaluate extraction on a
    # safe CASE operand and demand JSON true, never truthy strings/numbers.
    safe = case((func.json_valid(ub.User.view_settings) == 1,
                 ub.User.view_settings), else_='{}')
    return func.json_type(safe, '$.preferences.share_book_ratings') == 'true'


def ratings_for_books(session, user_id, book_ids):
    """At most two page-bounded queries; no user identities leave this helper."""
    ids = list(set(int(book_id) for book_id in book_ids))
    result = {book_id: {'personal_rating': None, 'household_rating': None}
              for book_id in ids}
    if user_id is None or not ids:
        return result
    for book_id, score in session.query(ub.BookRating.book_id, ub.BookRating.rating).filter(
            ub.BookRating.user_id == int(user_id), ub.BookRating.book_id.in_(ids)):
        result[book_id]['personal_rating'] = score if score > 0 else None
    if not db._sqlite_json_available(session, session):
        # Bare-metal builds without JSON1 keep the same feature and consent
        # boundary. Cast raw settings to text so malformed legacy JSON cannot
        # fail ORM deserialization; only this page's positive scores are read.
        totals = {}
        for book_id, score, raw in session.query(
                ub.BookRating.book_id, ub.BookRating.rating,
                cast(ub.User.view_settings, Text)).join(
                ub.User, ub.User.id == ub.BookRating.user_id).filter(
                ub.BookRating.book_id.in_(ids), ub.BookRating.rating > 0,
                ub.User.role.op('&')(constants.ROLE_ANONYMOUS) == 0):
            try:
                settings = json.loads(raw or '{}')
                preferences = settings.get('preferences') if isinstance(settings, dict) else None
                if not isinstance(preferences, dict) or preferences.get('share_book_ratings') is not True:
                    continue
            except (TypeError, ValueError):
                continue
            total, count = totals.get(book_id, (0, 0))
            totals[book_id] = total + score, count + 1
        for book_id, (total, count) in totals.items():
            result[book_id]['household_rating'] = total / count
        return result
    for book_id, average in session.query(
            ub.BookRating.book_id, func.avg(ub.BookRating.rating)).join(
            ub.User, ub.User.id == ub.BookRating.user_id).filter(
            ub.BookRating.book_id.in_(ids), ub.BookRating.rating > 0,
            _sharing_consent(),
            ub.User.role.op('&')(constants.ROLE_ANONYMOUS) == 0,
    ).group_by(ub.BookRating.book_id):
        result[book_id]['household_rating'] = float(average)
    return result
