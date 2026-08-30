# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shelf (collection) endpoints for /api/v1.

Read endpoints (list / detail) are new JSON views; write endpoints reuse the
shared, HTTP-free shelf core from cps/shelf.py (add_book_to_shelf,
remove_book_from_shelf, delete_shelf_helper, check_shelf_*_permissions,
check_shelf_is_unique) so the SPA and the legacy form UI can never diverge on
ordering, Kobo last_modified propagation, Hardcover sync, or permission rules.
"""
from datetime import datetime, timezone

from flask import jsonify, request
from sqlalchemy import or_, func
from sqlalchemy.exc import InvalidRequestError, OperationalError

from . import api_v1
from .serializers import serialize_shelf
from .books import _rows_to_items
from .. import calibre_db, config, db, ub, user_library
from ..cw_login import current_user
from ..sort_orders import BOOK_SORT_ORDERS
from ..usermanagement import login_required_if_no_ano
from ..shelf import (
    check_shelf_view_permissions,
    check_shelf_edit_permissions,
    check_shelf_is_unique,
    delete_shelf_helper,
    add_book_to_shelf,
    prepare_user_shelf_add,
    remove_book_from_shelf,
    compute_shelf_positions,
    queue_hardcover_sync,
    _shelf_book_count,
    sort_shelves_for_user,
    SHELF_OK,
    SHELF_ALREADY_PRESENT,
    SHELF_INVALID_BOOK,
    SHELF_NOT_IN_LIBRARY,
    SHELF_MANAGED_MEMBERSHIP_REFUSAL,
    SHELF_NOT_PRESENT,
)


def _uid():
    """Current user's int id, or None for the anonymous-browse guest."""
    return int(current_user.id) if current_user.is_authenticated else None


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


# ── List ─────────────────────────────────────────────────────────────────────

@api_v1.route("/shelves")
@login_required_if_no_ano
def list_shelves():
    """All shelves visible to the caller: their own private shelves plus every
    public shelf, ordered by the user's configured shelf order."""
    uid = _uid()
    visibility = ub.Shelf.is_public == 1
    if uid is not None:
        visibility = or_(ub.Shelf.user_id == uid, ub.Shelf.is_public == 1)

    shelves = ub.session.query(ub.Shelf).filter(visibility).all()
    sort_shelves_for_user(shelves, current_user)

    items = [
        serialize_shelf(s, _shelf_book_count(s, current_user), is_owner=(s.user_id == uid))
        for s in shelves
    ]
    return jsonify({"items": items})


# ── Detail (+ ordered books) ─────────────────────────────────────────────────

@api_v1.route("/shelves/<int:shelf_id>")
@login_required_if_no_ano
def shelf_detail(shelf_id):
    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.id == shelf_id).first()
    if shelf is None:
        return _err("not_found", "Shelf not found", 404)
    if not check_shelf_view_permissions(shelf):
        return _err("forbidden", "You are not allowed to view this shelf", 403)

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", config.config_books_per_page, type=int)

    # Shelf sorting is view-only: "stored", an unknown value, and the two
    # app-DB download-count sorts all retain the manual BookShelf order. Every
    # metadata sort comes from the shared ORDER BY map used by the catalog.
    sort = request.args.get("sort", "stored")
    order = BOOK_SORT_ORDERS.get(sort)
    if order is None or sort in ("hotdesc", "hotasc"):
        order = [ub.BookShelf.order.asc()]

    # Author sorting uses Series as a deterministic tiebreaker. fill_indexpage
    # greedily consumes join varargs 3, then 2, then 1 from the front, so this
    # three-argument group must precede the two-argument BookShelf group.
    joins = []
    if sort in ("authaz", "authza"):
        joins.extend((
            db.books_series_link,
            db.Books.id == db.books_series_link.c.book,
            db.Series,
        ))
    joins.extend((ub.BookShelf, ub.BookShelf.book_id == db.Books.id))

    # Same ACL/archive-filtered fetch as the HTML shelf view, with read/archived
    # joined. Unlike the classic sort action, this never rewrites BookShelf.
    entries, _random, pagination = calibre_db.fill_indexpage(
        page, per_page, db.Books,
        ub.BookShelf.shelf == shelf_id,
        order,
        True, config.config_read_column,
        *joins,
    )

    body = serialize_shelf(shelf, pagination.total_count, is_owner=(shelf.user_id == _uid()))
    body.update({
        "items": _rows_to_items(entries),
        "page": pagination.page,
        "per_page": pagination.per_page,
        "total": pagination.total_count,
        "can_edit": check_shelf_edit_permissions(shelf),
    })
    return jsonify(body)


# ── Per-book membership (for the "add to shelf" toggle UI) ───────────────────

@api_v1.route("/books/<int:book_id>/shelves")
@login_required_if_no_ano
def book_shelf_membership(book_id):
    """Which of the caller's visible shelves currently contain ``book_id``.
    Lets the add-to-shelf menu render toggles without N membership probes."""
    uid = _uid()
    visibility = ub.Shelf.is_public == 1
    if uid is not None:
        visibility = or_(ub.Shelf.user_id == uid, ub.Shelf.is_public == 1)

    rows = (ub.session.query(ub.BookShelf.shelf)
            .join(ub.Shelf, ub.Shelf.id == ub.BookShelf.shelf)
            .filter(ub.BookShelf.book_id == book_id)
            .filter(visibility)
            .all())
    return jsonify({"shelf_ids": [r[0] for r in rows]})


# ── Create ───────────────────────────────────────────────────────────────────

@api_v1.route("/shelves", methods=["POST"])
@login_required_if_no_ano
def create_shelf_api():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return _err("invalid_request", "Shelf name is required", 400)

    is_public = 1 if data.get("is_public") else 0
    if is_public and not current_user.role_edit_shelfs():
        return _err("forbidden", "You are not allowed to create a public shelf", 403)
    if not check_shelf_is_unique(name, is_public):
        return _err("conflict", "A shelf with that name already exists", 409)

    shelf = ub.Shelf(name=name, is_public=is_public, user_id=int(current_user.id))
    if data.get("kobo_sync") and config.config_kobo_sync:
        shelf.kobo_sync = True
    try:
        ub.session.add(shelf)
        ub.session.commit()
    except (OperationalError, InvalidRequestError) as e:
        ub.session.rollback()
        return _err("db_error", "Could not create shelf: %s" % getattr(e, "orig", e), 500)

    return jsonify(serialize_shelf(shelf, 0, is_owner=True)), 201


# ── Update (rename / visibility / kobo_sync) ─────────────────────────────────

@api_v1.route("/shelves/<int:shelf_id>", methods=["POST"])
@login_required_if_no_ano
def update_shelf_api(shelf_id):
    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.id == shelf_id).first()
    if shelf is None:
        return _err("not_found", "Shelf not found", 404)
    if not check_shelf_edit_permissions(shelf):
        return _err("forbidden", "You are not allowed to edit this shelf", 403)

    data = request.get_json(silent=True) or {}

    # Resolve the target visibility first so a same-call rename is checked for
    # uniqueness against the *new* public/private scope.
    target_public = shelf.is_public
    if "is_public" in data:
        target_public = 1 if data["is_public"] else 0
        if target_public and not current_user.role_edit_shelfs():
            return _err("forbidden", "You are not allowed to make a shelf public", 403)

    if "name" in data:
        name = (data["name"] or "").strip()
        if not name:
            return _err("invalid_request", "Shelf name cannot be empty", 400)
        if not check_shelf_is_unique(name, target_public, shelf_id):
            return _err("conflict", "A shelf with that name already exists", 409)
        shelf.name = name

    if "is_public" in data:
        shelf.is_public = target_public

    if "kobo_sync" in data and config.config_kobo_sync:
        shelf.kobo_sync = bool(data["kobo_sync"])
        if shelf.kobo_sync:
            # Clear any pending tombstone so a re-enabled shelf re-syncs to Kobo.
            ub.session.query(ub.ShelfArchive).filter(
                ub.ShelfArchive.user_id == int(current_user.id),
                ub.ShelfArchive.uuid == shelf.uuid,
            ).delete()

    shelf.last_modified = datetime.now(timezone.utc)
    try:
        ub.session.merge(shelf)
        ub.session.commit()
    except (OperationalError, InvalidRequestError) as e:
        ub.session.rollback()
        return _err("db_error", "Could not update shelf: %s" % getattr(e, "orig", e), 500)

    return jsonify(serialize_shelf(shelf, _shelf_book_count(shelf, current_user),
                                   is_owner=(shelf.user_id == _uid())))


# ── Delete ───────────────────────────────────────────────────────────────────

@api_v1.route("/shelves/<int:shelf_id>/delete", methods=["POST"])
@login_required_if_no_ano
def delete_shelf_api(shelf_id):
    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.id == shelf_id).first()
    if shelf is None:
        return _err("not_found", "Shelf not found", 404)
    # delete_shelf_helper re-checks edit permission and returns False if denied.
    try:
        if not delete_shelf_helper(shelf):
            return _err("forbidden", "You are not allowed to delete this shelf", 403)
    except InvalidRequestError as e:
        ub.session.rollback()
        return _err("db_error", "Could not delete shelf: %s" % getattr(e, "orig", e), 500)
    return "", 204


# ── Add / remove a book ──────────────────────────────────────────────────────

@api_v1.route("/shelves/<int:shelf_id>/books/<int:book_id>", methods=["POST"])
@login_required_if_no_ano
def add_book_to_shelf_api(shelf_id, book_id):
    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.id == shelf_id).first()
    if shelf is None:
        return _err("not_found", "Shelf not found", 404)
    if not check_shelf_edit_permissions(shelf):
        return _err("forbidden", "You are not allowed to add to this shelf", 403)

    try:
        prepare_user_shelf_add(book_id)
    except user_library.UserLibraryBookNotFound as ex:
        return _err("not_found", str(ex), 404)
    except user_library.UserLibraryError:
        return _err(
            "library_membership_rejected",
            SHELF_MANAGED_MEMBERSHIP_REFUSAL,
            403,
        )

    try:
        status, message = add_book_to_shelf(shelf, book_id)
    except (OperationalError, InvalidRequestError) as e:
        ub.session.rollback()
        return _err("db_error", "Database error: %s" % getattr(e, "orig", e), 500)

    if status == SHELF_INVALID_BOOK:
        return _err("not_found", message, 404)
    if status == SHELF_NOT_IN_LIBRARY:
        return _err("library_membership_required", message, 409)
    if status == SHELF_ALREADY_PRESENT:
        return _err("conflict", message, 409)
    return jsonify({"shelf_id": shelf_id, "book_id": book_id, "on_shelf": True})


@api_v1.route("/shelves/<int:shelf_id>/books/<int:book_id>/delete", methods=["POST"])
@login_required_if_no_ano
def remove_book_from_shelf_api(shelf_id, book_id):
    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.id == shelf_id).first()
    if shelf is None:
        return _err("not_found", "Shelf not found", 404)
    if not check_shelf_edit_permissions(shelf):
        return _err("forbidden", "You are not allowed to remove from this shelf", 403)

    try:
        status, message = remove_book_from_shelf(shelf, book_id)
    except (OperationalError, InvalidRequestError) as e:
        ub.session.rollback()
        return _err("db_error", "Database error: %s" % getattr(e, "orig", e), 500)

    if status == SHELF_NOT_PRESENT:
        return _err("not_found", message, 404)
    return "", 204


# ── Reorder books within a shelf ──────────────────────────────────────────────

@api_v1.route("/shelves/<int:shelf_id>/order", methods=["POST"])
@login_required_if_no_ano
def reorder_shelf_books_api(shelf_id):
    """Persist a new book order for a shelf. Body: {"order": [book_id, ...]}.
    Reuses compute_shelf_positions so a stale client can't orphan a book's slot
    (unknown ids dropped, missing ids keep their tail order) — same core the
    legacy drag-grid uses."""
    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.id == shelf_id).first()
    if shelf is None:
        return _err("not_found", "Shelf not found", 404)
    if not check_shelf_edit_permissions(shelf):
        return _err("forbidden", "You are not allowed to edit this shelf", 403)

    data = request.get_json(silent=True) or {}
    ordered = data.get("order")
    if not isinstance(ordered, list):
        return _err("invalid_request", "order must be a list of book ids", 400)
    ordered_ids = [int(x) for x in ordered if str(x).lstrip("-").isdigit()]

    books_in_shelf = (ub.session.query(ub.BookShelf)
                      .filter(ub.BookShelf.shelf == shelf_id)
                      .order_by(ub.BookShelf.order.asc()).all())
    available_ids = [e.book_id for e in books_in_shelf]
    positions = compute_shelf_positions(ordered_ids, available_ids)

    changed = False
    for entry in books_in_shelf:
        new_order = positions[entry.book_id]
        if entry.order != new_order:
            entry.order = new_order
            changed = True
    try:
        if changed:
            shelf.last_modified = datetime.now(timezone.utc)
            ub.session.merge(shelf)
            ub.session.commit()
    except (OperationalError, InvalidRequestError) as e:
        ub.session.rollback()
        return _err("db_error", "Could not save order: %s" % getattr(e, "orig", e), 500)
    return jsonify({"ok": True})


# ── Add a whole series to a shelf ─────────────────────────────────────────────

@api_v1.route("/shelves/<int:shelf_id>/series/<int:series_id>", methods=["POST"])
@login_required_if_no_ano
def add_series_to_shelf_api(shelf_id, series_id):
    """Add every visible book of a series to a shelf in series_index order
    (fork #334). Books already present are skipped; Hardcover sync is queued
    as a background task. Mirrors cps/shelf.py add_series_to_shelf."""
    shelf = ub.session.query(ub.Shelf).filter(ub.Shelf.id == shelf_id).first()
    if shelf is None:
        return _err("not_found", "Shelf not found", 404)
    if not check_shelf_edit_permissions(shelf):
        return _err("forbidden", "You are not allowed to edit this shelf", 403)
    series = calibre_db.session.query(db.Series).filter(db.Series.id == series_id).first()
    if series is None:
        return _err("not_found", "Series not found", 404)

    books = (calibre_db.session.query(db.Books)
             .filter(db.Books.series.any(db.Series.id == series_id))
             .filter(calibre_db.common_filters())
             .order_by(db.Books.series_index.asc(), db.Books.id.asc()).all())
    existing = {row.book_id for row in
                ub.session.query(ub.BookShelf.book_id).filter(ub.BookShelf.shelf == shelf_id).all()}
    to_add = [b for b in books if b.id not in existing]
    if not to_add:
        return jsonify({"added": 0})

    max_order = ub.session.query(func.max(ub.BookShelf.order)).filter(
        ub.BookShelf.shelf == shelf_id).scalar() or 0
    for book in to_add:
        max_order += 1
        shelf.books.append(ub.BookShelf(shelf=shelf.id, book_id=book.id, order=max_order))
    shelf.last_modified = datetime.now(timezone.utc)
    try:
        ub.session.merge(shelf)
        ub.session.commit()
    except (OperationalError, InvalidRequestError) as e:
        ub.session.rollback()
        return _err("db_error", "Could not add series: %s" % getattr(e, "orig", e), 500)
    queue_hardcover_sync(shelf, [b.id for b in to_add])
    return jsonify({"added": len(to_add)})
