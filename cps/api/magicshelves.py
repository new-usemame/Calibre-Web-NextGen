# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Magic-shelf (smart collection) read endpoints for /api/v1.

List the user's smart shelves and serve a shelf's matching books — reusing
cps/magic_shelf.build_query_from_rules (the same rule→SQL engine the legacy view
uses). Create/edit/duplicate/delete reuse the existing /magicshelf routes.
"""
from datetime import datetime, timezone

from flask import jsonify, request
from flask_babel import get_locale
from flask_babel import gettext as _
from sqlalchemy.exc import SQLAlchemyError

from . import api_v1
from .books import _row_to_item
from .. import ub, config, db, calibre_db, logger, magic_shelf
from ..cw_login import current_user
from ..usermanagement import login_required_if_no_ano, user_login_required

log = logger.create()


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def _uid():
    return int(current_user.id) if current_user.is_authenticated else None


def _shelf_item(shelf, uid):
    """Serialize a shelf with request-local display text.

    Built-in shelf names are canonical English database identity, while custom
    shelf names are literal user data.  The translation helper distinguishes
    them so changing locale never mutates or accidentally translates a user's
    own shelf.
    """
    return {
        "id": shelf.id,
        "name": magic_shelf.system_magic_shelf_display_name(shelf),
        "icon": shelf.icon or "🪄",
        "is_public": bool(shelf.is_public),
        "is_owner": shelf.user_id == uid,
        "is_system": bool(getattr(shelf, "is_system", False)),
        "kobo_sync": bool(getattr(shelf, "kobo_sync", False)),
    }


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
    items = [_shelf_item(s, uid) for s in shelves]
    return jsonify({"items": items})


@api_v1.route("/magicshelves/rule-schema")
@user_login_required
def magic_shelf_rule_schema():
    """The engine-owned field/operator contract consumed by both editors."""
    return jsonify(magic_shelf.build_rule_schema_for_locale(get_locale()))


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
    display_name = magic_shelf.system_magic_shelf_display_name(shelf)
    if query_filter is None:
        return jsonify({"id": shelf.id, "name": display_name, "icon": shelf.icon or "🪄",
                        "is_system": bool(getattr(shelf, "is_system", False)),
                        "is_owner": (shelf.user_id == uid),
                        "kobo_sync": bool(getattr(shelf, "kobo_sync", False)),
                        "items": [], "page": 1, "per_page": per_page, "total": 0})

    series_join = (db.books_series_link, db.Books.id == db.books_series_link.c.book, db.Series)
    entries, _random, pagination = calibre_db.fill_indexpage(
        page, per_page, db.Books, query_filter, [db.Books.timestamp.desc()],
        True, config.config_read_column, *series_join)
    return jsonify({
        "id": shelf.id, "name": display_name, "icon": shelf.icon or "🪄",
        "is_system": bool(getattr(shelf, "is_system", False)),
        "is_owner": (shelf.user_id == uid),
        "kobo_sync": bool(getattr(shelf, "kobo_sync", False)),
        # rules included so the builder can load this shelf for editing
        "rules": shelf.rules or {"condition": "AND", "rules": []},
        "items": [_row_to_item(e) for e in entries],
        "page": pagination.page, "per_page": pagination.per_page, "total": pagination.total_count,
    })


@api_v1.route("/magicshelf/<int:shelf_id>/kobo-sync", methods=["POST"])
@user_login_required
def set_magic_shelf_kobo_sync(shelf_id):
    """Flip a smart shelf's Kobo-sync mark (#870).

    The classic /magicshelf/<id>/edit route only accepts a whole-shelf save
    (name + icon + rules + flags), so the SPA's shelf view had no way to toggle
    this one flag without round-tripping the rule set. This is that narrow
    write; everything downstream (collection materialisation, DeletedTag
    tombstones) is already wired in cps/kobo.py.

    Owner-only: cps/kobo.py selects magic shelves by ``user_id == current_user``,
    so marking someone else's public shelf could never sync to your device.
    ``last_modified`` is bumped on every flip because the Kobo tag/tombstone
    payloads carry it as the change timestamp — without it a device that
    already synced would ignore the change.
    """
    shelf = ub.session.query(ub.MagicShelf).get(shelf_id)
    if shelf is None:
        return _err("not_found", "Smart shelf not found", 404)
    if shelf.user_id != _uid():
        return _err("forbidden", "You are not allowed to edit this shelf", 403)
    if not config.config_kobo_sync:
        return _err("forbidden", "Kobo sync is not enabled on this server", 403)

    data = request.get_json(silent=True)
    # A JSON boolean, not Python truthiness: bool("false") is True, so coercing
    # would let {"kobo_sync": "false"} perform the opposite write. A top-level
    # scalar body (`42`) would also raise on the membership test, so require a
    # mapping before looking inside it.
    if not isinstance(data, dict) or "kobo_sync" not in data:
        return _err("invalid_request", "kobo_sync is required", 400)
    if not isinstance(data["kobo_sync"], bool):
        return _err("invalid_request", "kobo_sync must be a boolean", 400)
    enabled = data["kobo_sync"]

    shelf.kobo_sync = enabled
    shelf.last_modified = datetime.now(timezone.utc)
    try:
        ub.session.commit()
    except SQLAlchemyError:
        # ub.session is a long-lived global session, so a commit that fails
        # without a rollback leaves it in a pending-rollback state that breaks
        # unrelated later requests. Catch the whole family, never just the two
        # we happened to think of. The driver error is logged, not returned —
        # it can carry schema and environment detail.
        ub.session.rollback()
        log.exception("magic shelf %s: kobo_sync commit failed", shelf_id)
        return _err("db_error", "Could not update shelf", 500)

    body = {"id": shelf.id, "kobo_sync": enabled}
    # Mirror of the classic edit route: intent is stored, but it stays inert
    # until an admin enables the magic-shelf half of Kobo sync (#359).
    if enabled and not config.config_kobo_sync_magic_shelves:
        body["warning"] = _("Kobo sync for Magic Shelves is disabled globally — "
                            "this shelf won't reach your Kobo until 'Sync Magic "
                            "Shelves to Kobo' is enabled in CWA Settings.")
    return jsonify(body)
