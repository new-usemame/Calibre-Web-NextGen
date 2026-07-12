# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Entity-list browse endpoints for /api/v1."""
from sqlalchemy import func, text

from . import api_v1
from .. import calibre_db, db
from ..usermanagement import login_required_if_no_ano


@api_v1.route("/authors")
@login_required_if_no_ano
def list_authors():
    rows = (calibre_db.session.query(db.Authors, func.count('books_authors_link.book').label('count'))
            .join(db.books_authors_link)
            .join(db.Books)
            .filter(calibre_db.common_filters())
            .group_by(text('books_authors_link.author'))
            .order_by(func.ng_sort_key(db.Authors.sort), db.Authors.sort, db.Authors.id)
            .all())
    items = [{"id": a.id, "name": a.name.replace("|", ","), "count": cnt} for a, cnt in rows]
    return {"items": items}


@api_v1.route("/series")
@login_required_if_no_ano
def list_series():
    rows = (calibre_db.session.query(db.Series, func.count('books_series_link.book').label('count'))
            .join(db.books_series_link)
            .join(db.Books)
            .filter(calibre_db.common_filters())
            .group_by(text('books_series_link.series'))
            .order_by(func.ng_sort_key(db.Series.sort), db.Series.sort, db.Series.id)
            .all())
    items = [{"id": s.id, "name": s.name, "count": cnt} for s, cnt in rows]
    return {"items": items}


@api_v1.route("/tags")
@login_required_if_no_ano
def list_tags():
    rows = (calibre_db.session.query(db.Tags, func.count('books_tags_link.book').label('count'))
            .join(db.books_tags_link)
            .join(db.Books)
            .filter(calibre_db.common_filters())
            .group_by(db.Tags.id)
            .order_by(func.ng_sort_key(db.Tags.name), db.Tags.name, db.Tags.id)
            .all())
    items = [{"id": t.id, "name": t.name, "count": cnt} for t, cnt in rows]
    return {"items": items}


@api_v1.route("/publishers")
@login_required_if_no_ano
def list_publishers():
    rows = (calibre_db.session.query(db.Publishers, func.count(db.books_publishers_link.c.book).label('count'))
            .join(db.books_publishers_link, db.Publishers.id == db.books_publishers_link.c.publisher)
            .join(db.Books, db.books_publishers_link.c.book == db.Books.id)
            .filter(calibre_db.common_filters())
            .group_by(db.Publishers.id)
            .order_by(func.ng_sort_key(db.Publishers.sort), db.Publishers.sort, db.Publishers.id)
            .all())
    items = [{"id": p.id, "name": p.name, "count": cnt} for p, cnt in rows]
    return {"items": items}


@api_v1.route("/languages")
@login_required_if_no_ano
def list_languages():
    lang_list = calibre_db.speaking_language(with_count=True)
    # speaking_language returns [[Category, count], ...] where Category.id = lang_code, Category.name = display name
    items = [{"id": cat.id, "name": cat.name, "count": cnt} for cat, cnt in lang_list]
    return {"items": items}


@api_v1.route("/ratings")
@login_required_if_no_ano
def list_ratings():
    """Browse by star rating. Calibre stores rating as 0-10 (stars*2); the SPA
    filters books by the Ratings row id (matches list_books ?rating=)."""
    rows = (calibre_db.session.query(db.Ratings, func.count('books_ratings_link.book').label('count'))
            .join(db.books_ratings_link)
            .join(db.Books)
            .filter(calibre_db.common_filters())
            .group_by(text('books_ratings_link.rating'))
            .order_by(db.Ratings.rating.desc())
            .all())
    items = [{"id": r.id, "name": "%g★" % (r.rating / 2), "count": cnt} for r, cnt in rows]
    return {"items": items}


@api_v1.route("/formats")
@login_required_if_no_ano
def list_formats():
    """Browse by file format (EPUB, PDF, …). The format string is the id; the SPA
    filters books by it (matches list_books ?format=)."""
    rows = (calibre_db.session.query(db.Data.format, func.count(db.Data.book).label('count'))
            .join(db.Books, db.Books.id == db.Data.book)
            .filter(calibre_db.common_filters())
            .group_by(db.Data.format)
            .order_by(db.Data.format)
            .all())
    items = [{"id": fmt, "name": fmt, "count": cnt} for fmt, cnt in rows]
    return {"items": items}
