# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Advanced search endpoints for /api/v1.

Reuses cps/search.py's build_adv_search_query (the same query builder the HTML
advanced-search view uses) so the structured search behaves identically across
the legacy UI and the SPA.
"""
from flask import jsonify, request
from sqlalchemy import func

from . import api_v1
from .books import SORT_MAP, _row_to_item
from .. import calibre_db, config, db
from ..cw_login import current_user
from ..usermanagement import login_required_if_no_ano
from ..search import build_adv_search_query

# SPA read-status value -> the term value build_adv_search_query expects.
_READ_STATUS = {"all": "Any", "read": "True", "unread": "False"}


def _as_str_list(value):
    """Coerce an incoming JSON value to a list of strings (ids/format codes).
    The query builders iterate these, so a missing field must become []."""
    if not value:
        return []
    if not isinstance(value, list):
        value = [value]
    return [str(v) for v in value]


def _json_to_term(data):
    """Translate the SPA's JSON search payload into the ``term`` dict shape that
    build_adv_search_query consumes (mirrors the HTML form field names)."""
    return {
        "title": data.get("title", "") or "",
        "authors": data.get("authors", "") or "",
        "publisher": data.get("publisher", "") or "",
        "comments": data.get("comments", "") or "",
        "publishstart": data.get("publishstart", "") or "",
        "publishend": data.get("publishend", "") or "",
        # NB: build_adv_search_query maps ratinghigh->rating_low internally
        # (an upstream quirk we preserve for parity); pass through verbatim.
        "ratinghigh": data.get("rating_high", "") or "",
        "ratinglow": data.get("rating_low", "") or "",
        "read_status": _READ_STATUS.get(data.get("read_status", "all"), "Any"),
        "include_tag": _as_str_list(data.get("include_tag")),
        "exclude_tag": _as_str_list(data.get("exclude_tag")),
        "include_serie": _as_str_list(data.get("include_serie")),
        "exclude_serie": _as_str_list(data.get("exclude_serie")),
        "include_language": _as_str_list(data.get("include_language")),
        "exclude_language": _as_str_list(data.get("exclude_language")),
        "include_extension": _as_str_list(data.get("include_extension")),
        "exclude_extension": _as_str_list(data.get("exclude_extension")),
        "include_shelf": _as_str_list(data.get("include_shelf")),
        "exclude_shelf": _as_str_list(data.get("exclude_shelf")),
    }


@api_v1.route("/search/options")
@login_required_if_no_ano
def search_options():
    """Picker options for the advanced-search form, in the exact id shape the
    query builder expects: tags/series by row id, languages by row id (NOT
    lang_code — that's what adv_search_language filters on), formats by code."""
    tags = (calibre_db.session.query(db.Tags)
            .order_by(func.ng_sort_key(db.Tags.name), db.Tags.name, db.Tags.id).all())
    series = (calibre_db.session.query(db.Series)
              .order_by(func.ng_sort_key(db.Series.sort), db.Series.sort, db.Series.id).all())
    languages = (calibre_db.session.query(db.Languages).all())
    formats = (calibre_db.session.query(db.Data.format).distinct().order_by(db.Data.format).all())

    from .. import isoLanguages
    from flask_babel import get_locale
    lang_items = []
    for lang in languages:
        try:
            name = isoLanguages.get_language_name(get_locale(), lang.lang_code)
        except Exception:
            name = lang.lang_code
        lang_items.append({"id": lang.id, "name": name})
    lang_items.sort(key=lambda x: x["name"].lower())

    return jsonify({
        "tags": [{"id": t.id, "name": t.name} for t in tags],
        "series": [{"id": s.id, "name": s.name} for s in series],
        "languages": lang_items,
        "formats": [row[0] for row in formats if row[0]],
    })


@api_v1.route("/search/advanced", methods=["POST"])
@login_required_if_no_ano
def advanced_search():
    data = request.get_json(silent=True) or {}
    page = max(1, int(data.get("page", 1) or 1))
    per_page = int(data.get("per_page", config.config_books_per_page) or config.config_books_per_page)
    order = SORT_MAP.get(data.get("sort", "new"), SORT_MAP["new"])

    term = _json_to_term(data)
    query, criteria = build_adv_search_query(term)
    # build_adv_search_query always adds a BookShelf outerjoin (shelf include/
    # exclude support), so a book on N shelves yields N identical result rows.
    # DISTINCT collapses them — the selected (Books, is_archived, read_status)
    # tuple is identical per book — so total and items agree.
    query = query.distinct().order_by(*order)

    total = query.count()
    rows = query.offset((page - 1) * per_page).limit(per_page).all()

    # build_adv_search_query returns the criteria summary as a joined string when
    # any filter ran, or an empty list when none did — normalize to a string.
    criteria_str = criteria if isinstance(criteria, str) else ""
    # The shared builder renders the read-status criterion as the raw term value
    # ("Read Status = 'True'/'False'"); humanize it for the summary line. Display
    # only — English best-effort; the structured filter itself is unaffected.
    criteria_str = (criteria_str
                    .replace("Read Status = 'True'", "Read")
                    .replace("Read Status = 'False'", "Unread"))

    return jsonify({
        "items": [_row_to_item(r) for r in rows],
        "page": page,
        "per_page": per_page,
        "total": total,
        "criteria": criteria_str,  # human-readable "you searched for…" summary
    })
