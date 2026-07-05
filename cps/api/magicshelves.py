# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Magic-shelf (smart collection) read endpoints for /api/v1.

List the user's smart shelves and serve a shelf's matching books — reusing
cps/magic_shelf.build_query_from_rules (the same rule→SQL engine the legacy view
uses). Create/edit/duplicate/delete reuse the existing /magicshelf routes.
"""
from flask import jsonify, request

from . import api_v1
from .books import _row_to_item
from .. import ub, config, db, calibre_db, logger, magic_shelf
from ..cw_login import current_user
from ..usermanagement import login_required_if_no_ano

log = logger.create()


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def _uid():
    return int(current_user.id) if current_user.is_authenticated else None


@api_v1.route("/magicshelves")
@login_required_if_no_ano
def list_magic_shelves():
    """The caller's visible smart shelves — own + public, minus the ones the
    user hid via the classic "Magic Shelves Visibility" settings (#667).

    Delegates to magic_shelf.get_visible_magic_shelves_for_user so the SPA
    honours the same hidden_magic_shelf_templates filtering the classic sidebar
    (cps/__init__.py) and OPDS already apply — one source of truth. Anonymous
    callers have no user row to hide against, so they get the public shelves.
    """
    uid = _uid()
    if uid is not None:
        shelves = magic_shelf.get_visible_magic_shelves_for_user(uid)
        shelves.sort(key=lambda s: (s.name or "").casefold())
    else:
        shelves = ub.session.query(ub.MagicShelf).filter(
            ub.MagicShelf.is_public == 1).order_by(ub.MagicShelf.name).all()
    items = [{
        "id": s.id, "name": s.name, "icon": s.icon or "🪄",
        "is_public": bool(s.is_public), "is_owner": (s.user_id == uid),
    } for s in shelves]
    return jsonify({"items": items})


@api_v1.route("/magicshelf/<int:shelf_id>")
@login_required_if_no_ano
def magic_shelf_books(shelf_id):
    """Books matching a smart shelf's rules (paginated)."""
    shelf = ub.session.query(ub.MagicShelf).get(shelf_id)
    if shelf is None:
        return _err("not_found", "Smart shelf not found", 404)
    uid = _uid()
    if shelf.user_id != uid and not shelf.is_public:
        return _err("forbidden", "You are not allowed to view this shelf", 403)

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", config.config_books_per_page, type=int)

    try:
        query_filter = magic_shelf.build_query_from_rules(shelf.rules, user_id=uid)
    except Exception:
        log.error("Bad magic-shelf rules for shelf %s", shelf_id, exc_info=True)
        query_filter = None
    if query_filter is None:
        return jsonify({"id": shelf.id, "name": shelf.name, "icon": shelf.icon or "🪄",
                        "is_owner": (shelf.user_id == uid),
                        "items": [], "page": 1, "per_page": per_page, "total": 0})

    series_join = (db.books_series_link, db.Books.id == db.books_series_link.c.book, db.Series)
    entries, _random, pagination = calibre_db.fill_indexpage(
        page, per_page, db.Books, query_filter, [db.Books.timestamp.desc()],
        True, config.config_read_column, *series_join)
    return jsonify({
        "id": shelf.id, "name": shelf.name, "icon": shelf.icon or "🪄",
        "is_owner": (shelf.user_id == uid),
        # rules included so the builder can load this shelf for editing
        "rules": shelf.rules or {"condition": "AND", "rules": []},
        "items": [_row_to_item(e) for e in entries],
        "page": pagination.page, "per_page": pagination.per_page, "total": pagination.total_count,
    })
