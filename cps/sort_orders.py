# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
#
#  This file is part of the Calibre-Web-NextGen project.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.
"""The ORDER BY behind every book list, in one place.

Two things made this a shared module rather than a constant in each caller.

**Ties.** Every list here pages with LIMIT/OFFSET — ``fill_indexpage``,
``get_search_results``, the ``/api/v1`` lists. When the leading columns of an
ORDER BY tie, SQLite orders the tied rows however its plan happens to walk
them, and that is not a promise it keeps: measured on a table of twenty books
sharing one timestamp, "Newest" returned them oldest-first because the plan
scanned the rowid, then returned them newest-first once an index on the sort
column existed. So the user sees a list that is sorted, then is not (fork
#1331). Ties are the normal case, not an edge one: a bulk ingest gives every
book in the run one timestamp, calibre writes ``0101-01-01`` for a book with no
publication date, and every book outside a series carries series_index 1.0.

Each order therefore ends on a unique column, running the same way as the sort
the user asked for, so the order is total and the pages line up.

**One copy.** The classic UI and the new UI's API each carried their own map of
the same sorts, and both were missing the same tiebreakers. A sort fixed in one
would have stayed broken in the other.

``hotdesc``/``hotasc`` are the exception to ``Books.id``: those run against the
app database, grouped on ``Downloads.book_id``, where ``Books`` is not in
scope. The group key is unique per row of that result, so it is the tiebreaker
there.

**Per-user orders.** ``recent`` is not in the map, because the map is built once
at import time and this order needs the viewer's id. ``book_sort_order`` builds
it per request instead; the callers that iterate the map (the #1331 invariants,
``custom_column_sort``'s magic-shelf allowlist) therefore never see a sort whose
meaning depends on who is asking. See ``recent_sort_order``.
"""
from sqlalchemy import select
from sqlalchemy.orm import aliased
from sqlalchemy.sql.expression import func
from sqlalchemy.sql.functions import coalesce

from . import db, ub


BOOK_SORT_ORDERS = {
    "new": [db.Books.timestamp.desc(), db.Books.id.desc()],
    "old": [db.Books.timestamp, db.Books.id],
    "abc": [func.ng_sort_key(db.Books.sort), db.Books.sort, db.Books.id],
    "zyx": [func.ng_sort_key(db.Books.sort).desc(), db.Books.sort.desc(), db.Books.id.desc()],
    "pubnew": [db.Books.pubdate.desc(), db.Books.id.desc()],
    "pubold": [db.Books.pubdate, db.Books.id],
    "modifiednew": [db.Books.last_modified.desc(), db.Books.id.desc()],
    "modifiedold": [db.Books.last_modified, db.Books.id],
    "authaz": [func.ng_sort_key(db.Books.author_sort), db.Books.author_sort,
               func.ng_sort_key(db.Series.name), db.Series.name,
               db.Books.series_index, db.Books.id],
    "authza": [func.ng_sort_key(db.Books.author_sort).desc(), db.Books.author_sort.desc(),
               func.ng_sort_key(db.Series.name).desc(), db.Series.name.desc(),
               db.Books.series_index.desc(), db.Books.id.desc()],
    # Series reading order, so a series reads 1, 2, 3… rather than newest-first
    # (fork #573). Every list_books path already joins db.Series.
    "seriesasc": [db.Books.series_index.asc(), db.Books.id.asc()],
    "seriesdesc": [db.Books.series_index.desc(), db.Books.id.desc()],
    # Download counts, queried on the app database — see the module docstring
    # for why these tiebreak on the group key instead of Books.id.
    "hotdesc": [func.count(ub.Downloads.book_id).desc(), ub.Downloads.book_id.desc()],
    "hotasc": [func.count(ub.Downloads.book_id).asc(), ub.Downloads.book_id.asc()],
}

#: Used when a request names a sort this build does not have.
DEFAULT_SORT = "new"

#: The per-user order. Not a key of :data:`BOOK_SORT_ORDERS` — see the module
#: docstring and :func:`recent_sort_order`.
RECENT_SORT = "recent"

#: Stand-in clock for reading activity whose own timestamp is missing. Both
#: clocks arrived as ``ALTER TABLE … ADD COLUMN`` with no default —
#: ``kobo_bookmark.created_at`` (``ub.py``, ``migrate_kobo_bookmark_created_at``)
#: and ``bookmark.updated_at`` ("Keep old CFI timestamps unknown; future saves
#: record their own clock") — so on an upgraded install *every* row older than
#: the upgrade carries NULL there. That still means the user read the book, so
#: it has to outrank a book they never opened, while never outranking activity
#: that does carry a clock.
_ACTIVITY_WHEN_UNKNOWN = "0001-01-01 00:00:00"

#: What the recency expression evaluates to for a book with no activity. Sorts
#: below every stored timestamp *and* below ``_ACTIVITY_WHEN_UNKNOWN``, so the
#: whole unread group falls through to the date-added tiebreakers together.
_NO_ACTIVITY = ""


def _reading_activity(user_id):
    """The newest moment this build recorded ``user_id`` reading each book.

    One SQL expression, evaluated per row of whatever list is being paged, so
    the endpoint keeps paginating in SQL. Three correlated aggregates, one per
    carrier the readers actually write:

    * ``book_read_link`` — the read tri-state and its clocks. Written by
      ``kosync.update_book_read_status`` (KOReader sync, and the web reader,
      which reuses it), by ``kobo.HandleStateRequest``'s StatusInfo branch and
      by ``helper.edit_book_read_status`` (the Read checkmark).
    * ``kobo_bookmark`` — the cross-device position, advanced by
      ``device_reading_position.advance_kobo_bookmark`` for a Kobo state PUT, a
      KOReader sync and a web-reader save alike.
    * ``bookmark`` — the web reader's own epub.js CFI, stamped on every save
      including the ones that carry no percentage (``cps/api/reader.py``).

    Each is gated on POSITIVE evidence rather than on the row existing, because
    ``kobo.HandleStateRequest`` seeds the entire ReadBook/KoboReadingState/
    KoboBookmark/KoboStatistics graph — with fresh clocks and no progress — on a
    **GET**. Ungated, a Kobo that merely listed the library would reorder it.
    ``helper.reset_reading_position`` ("mark unread") clears exactly what these
    gates read, so a restarted book leaves the read group.

    ``kosync_progress`` is deliberately not a fourth carrier: it is keyed by
    file checksum in app.db while the checksums live in metadata.db, and its PUT
    handler already mirrors every matched book into the two carriers above.
    """
    read = aliased(ub.ReadBook)
    state = aliased(ub.KoboReadingState)
    kobo_position = aliased(ub.KoboBookmark)
    web_position = aliased(ub.Bookmark)

    # SQLite's multi-argument max() is the scalar one and returns NULL if ANY
    # argument is NULL, hence the inner coalesce; the outer max() is the
    # aggregate over the matching rows and yields NULL when there are none.
    read_activity = (
        select(func.max(func.max(
            coalesce(read.last_modified, _ACTIVITY_WHEN_UNKNOWN),
            coalesce(read.last_time_started_reading, _ACTIVITY_WHEN_UNKNOWN),
        )))
        .where(read.user_id == user_id,
               read.book_id == db.Books.id,
               read.read_status != ub.ReadBook.STATUS_UNREAD)
        .correlate(db.Books)
        .scalar_subquery()
    )
    kobo_activity = (
        select(func.max(func.max(
            coalesce(kobo_position.last_modified, _ACTIVITY_WHEN_UNKNOWN),
            coalesce(kobo_position.created_at, _ACTIVITY_WHEN_UNKNOWN),
        )))
        .select_from(kobo_position)
        .join(state, kobo_position.kobo_reading_state_id == state.id)
        .where(state.user_id == user_id,
               state.book_id == db.Books.id,
               coalesce(kobo_position.progress_percent, 0) > 0)
        .correlate(db.Books)
        .scalar_subquery()
    )
    # Aggregated rather than joined: ``bookmark`` is per (user, book, FORMAT),
    # so an outer join would hand the list the same book once per format.
    web_activity = (
        select(func.max(
            coalesce(web_position.updated_at, _ACTIVITY_WHEN_UNKNOWN)))
        .where(web_position.user_id == user_id,
               web_position.book_id == db.Books.id)
        .correlate(db.Books)
        .scalar_subquery()
    )
    return func.max(
        coalesce(read_activity, _NO_ACTIVITY),
        coalesce(kobo_activity, _NO_ACTIVITY),
        coalesce(web_activity, _NO_ACTIVITY),
    )


def recent_sort_order(user_id):
    """"Recent": what this user has been reading, then everything else.

    Books with reading activity come first, newest activity first. Books with
    none evaluate to ``_NO_ACTIVITY``, which is below every real timestamp, so
    they tie there and fall through to the date-added order — the whole unread
    half of the library reads exactly like "Newest". Ends on ``Books.id``
    descending like every other descending sort, so the order is total and the
    pages line up (#1331).
    """
    return [
        _reading_activity(int(user_id)).desc(),
        db.Books.timestamp.desc(),
        db.Books.id.desc(),
    ]


def viewer_id(user):
    """The signed-in reader behind this request, or ``None``.

    ``None`` is every shape that has no reading history to sort by, and the
    guest is the one worth spelling out: anonymous browse signs the visitor in
    as a *real* user row, so ``is_authenticated`` is True and ``id`` is a real
    id. ``ub.User.is_anonymous`` is ``role_anonymous()``, and that row is shared
    by every visitor — sorting by its history would show one stranger's reading
    to the next. The exception arm also covers a request with no login manager
    and the stripped-decorator unit contexts that hand in a stand-in object.
    """
    try:
        if (not user.is_authenticated) or user.is_anonymous:
            return None
        return int(user.id)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def book_sort_order(sort_param, user_id=None):
    """Return the ORDER BY for ``sort_param``, falling back to newest-first.

    :param sort_param: A key of :data:`BOOK_SORT_ORDERS`, :data:`RECENT_SORT`,
        or anything else.
    :param user_id: The viewer, for the per-user orders. ``None`` means there is
        no real user — anonymous browse, a background render — and the per-user
        orders degrade to the default rather than sorting by nobody's history.
    :return: A list of SQLAlchemy order expressions, never empty.
    """
    if sort_param == RECENT_SORT and user_id is not None:
        return recent_sort_order(user_id)
    return BOOK_SORT_ORDERS.get(sort_param, BOOK_SORT_ORDERS[DEFAULT_SORT])
