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
"""
from sqlalchemy.sql.expression import func

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


def book_sort_order(sort_param):
    """Return the ORDER BY for ``sort_param``, falling back to newest-first.

    :param sort_param: A key of :data:`BOOK_SORT_ORDERS`, or anything else.
    :return: A list of SQLAlchemy order expressions, never empty.
    """
    return BOOK_SORT_ORDERS.get(sort_param, BOOK_SORT_ORDERS[DEFAULT_SORT])
