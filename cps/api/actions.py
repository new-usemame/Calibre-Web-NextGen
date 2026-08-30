# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-book user actions for /api/v1: favorite, hide, archive, send-to-e-reader.

These mirror the legacy web.py routes (toggle_favorite / toggle_hidden /
toggle_archived / send_to_ereader) and reuse the same models + helpers so the SPA
and the Jinja UI never diverge. All are per-user actions, so they require a real
(non-anonymous) session — the anonymous-browse guest can't own favorites/hidden
state or send mail.
"""
from flask import jsonify, request

from . import api_v1
from .. import ub, config, calibre_db, user_library
from ..cw_login import current_user
from ..usermanagement import login_required_if_no_ano
from ..helper import send_mail, valid_email
from ..kobo_sync_status import change_archived_books, remove_synced_book
from ..services import device_delivery

BATCH_MEMBERSHIP_LIMIT = 200


def _err(code, message, status, **details):
    error = {"code": code, "message": message}
    error.update(details)
    return jsonify({"error": error}), status


def _require_real_user():
    """Per-user actions need a real login — reject the anonymous-browse guest."""
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    return None


def _book_or_404(book_id):
    return calibre_db.get_book(book_id)


@api_v1.route("/books/<int:book_id>/favorite", methods=["POST"])
@login_required_if_no_ano
def toggle_book_favorite(book_id):
    """Star/unstar a book for the current user (presence-based, fork #27)."""
    guard = _require_real_user()
    if guard:
        return guard
    favorite = (ub.session.query(ub.FavoriteBook)
                .filter(ub.FavoriteBook.user_id == int(current_user.id),
                        ub.FavoriteBook.book_id == book_id)
                .first())
    if favorite:
        ub.session.delete(favorite)
        favorited = False
    else:
        ub.session.add(ub.FavoriteBook(user_id=int(current_user.id), book_id=book_id))
        favorited = True
    ub.session_commit("Book {} favorite bit toggled".format(book_id))
    return jsonify({"favorited": favorited})


@api_v1.route("/books/<int:book_id>/my-library", methods=["PUT"])
@login_required_if_no_ano
def add_book_to_my_library(book_id):
    """Idempotently add a global book to the current user's membership set."""
    guard = _require_real_user()
    if guard:
        return guard
    try:
        user_library.add_book(current_user, book_id)
    except user_library.UserLibraryError as ex:
        return _err("library_membership_rejected", str(ex), 403)
    return jsonify({"in_my_library": True})


@api_v1.route("/books/<int:book_id>/my-library", methods=["GET"])
@login_required_if_no_ano
def my_library_removal_impact(book_id):
    """Describe shelf and Kobo effects before the SPA asks for confirmation."""
    guard = _require_real_user()
    if guard:
        return guard
    user_library.mark_response_user_specific()
    try:
        impact = user_library.removal_impact(current_user, book_id)
    except user_library.UserLibraryError as ex:
        return _err("library_membership_rejected", str(ex), 409)
    return jsonify(impact)


@api_v1.route("/books/<int:book_id>/my-library", methods=["DELETE"])
@login_required_if_no_ano
def remove_book_from_my_library(book_id):
    """Remove membership plus the user's ordinary shelf links.

    The response is the confirm/result contract for the SPA lane: it names
    affected shelves and makes the next-sync Kobo removal explicit.
    """
    guard = _require_real_user()
    if guard:
        return guard
    try:
        shelves = user_library.remove_book(current_user, book_id)
    except user_library.UserLibraryError as ex:
        return _err("library_membership_rejected", str(ex), 409)
    return jsonify({
        "in_my_library": False,
        "affected_shelves": shelves,
        "kobo_removal_on_next_sync": True,
        "reading_data_preserved": True,
    })


@api_v1.route("/books/my-library/batch", methods=["POST"])
@login_required_if_no_ano
def batch_my_library_membership():
    """Apply ordered, independently reportable membership operations.

    This is deliberately an HTTP batching layer over the single-book policy,
    not a bulk database path: every id calls ``add_book`` or ``remove_book``.
    Successful earlier items therefore stay committed when a later policy
    check fails, exactly as they would across sequential single-book requests.
    """
    guard = _require_real_user()
    if guard:
        return guard
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _err(
            "invalid_request", "A JSON object is required", 400
        )
    operation = data.get("operation")
    if operation not in ("add", "remove"):
        return _err(
            "invalid_request", "operation must be 'add' or 'remove'", 400
        )
    book_ids = data.get("book_ids")
    if not isinstance(book_ids, list) or not book_ids:
        return _err(
            "invalid_request", "book_ids must be a non-empty list", 400
        )
    if len(book_ids) > BATCH_MEMBERSHIP_LIMIT:
        return _err(
            "batch_too_large",
            "book_ids accepts at most {} items".format(
                BATCH_MEMBERSHIP_LIMIT
            ),
            400,
            max_items=BATCH_MEMBERSHIP_LIMIT,
        )
    if any(type(book_id) is not int or book_id <= 0 for book_id in book_ids):
        return _err(
            "invalid_request", "book_ids must contain positive integers", 400
        )

    user_library.mark_response_user_specific()
    results = []
    succeeded_ids = []
    failed_ids = []
    error_status = 403 if operation == "add" else 409

    for book_id in book_ids:
        try:
            if operation == "add":
                mutation = user_library.add_book(
                    current_user, book_id, return_result=True
                )
            else:
                mutation = user_library.remove_book(
                    current_user, book_id, return_result=True
                )
        except user_library.UserLibraryError as ex:
            failed_ids.append(book_id)
            results.append({
                "book_id": book_id,
                "status": "failed",
                "error": {
                    "code": "library_membership_rejected",
                    "message": str(ex),
                },
                "http_status": error_status,
            })
            continue

        item = {
            "book_id": book_id,
            "status": "succeeded",
            "changed": mutation.changed,
            "in_my_library": operation == "add",
        }
        if operation == "remove":
            item.update({
                "affected_shelves": list(mutation.affected_shelves),
                "kobo_removal_on_next_sync": True,
                "reading_data_preserved": True,
            })
        succeeded_ids.append(book_id)
        results.append(item)

    return jsonify({
        "operation": operation,
        "results": results,
        "succeeded_ids": succeeded_ids,
        "failed_ids": failed_ids,
        "succeeded": len(succeeded_ids),
        "failed": len(failed_ids),
        "partial_failure": bool(succeeded_ids and failed_ids),
    })


@api_v1.route("/books/<int:book_id>/archived", methods=["POST"])
@login_required_if_no_ano
def toggle_book_archived(book_id):
    """Archive/unarchive (sync-pause semantics). Reuses the legacy core so the
    Kobo synced-books bookkeeping stays identical."""
    guard = _require_real_user()
    if guard:
        return guard
    archived = change_archived_books(book_id, message="Book {} archive bit toggled".format(book_id))
    # Force a resync so the device picks up the archive change (matches legacy).
    remove_synced_book(book_id)
    return jsonify({"archived": bool(archived)})


@api_v1.route("/books/<int:book_id>/hidden", methods=["POST"])
@login_required_if_no_ano
def toggle_book_hidden(book_id):
    """Hide/unhide a book for the current user (fork #64). Hiding is gated on the
    admin feature flag (#319); unhiding is always allowed so an admin disabling
    the feature can't strand already-hidden books."""
    guard = _require_real_user()
    if guard:
        return guard
    data = request.get_json(silent=True)
    desired = None
    if isinstance(data, dict) and "hidden" in data:
        if not isinstance(data["hidden"], bool):
            return _err("invalid_request", "hidden must be a boolean", 400)
        desired = data["hidden"]
    existing = (ub.session.query(ub.UserHiddenBook)
                .filter(ub.UserHiddenBook.user_id == int(current_user.id),
                        ub.UserHiddenBook.book_id == int(book_id))
                .first())
    if existing:
        if desired is True:
            return jsonify({"hidden": True})
        ub.session.delete(existing)
        ub.session.commit()
        return jsonify({"hidden": False})
    if desired is False:
        return jsonify({"hidden": False})
    # Hide path — gated; a direct POST must not bypass the disabled feature.
    if not bool(getattr(config, "config_user_hide_enabled", False)):
        return _err("forbidden", "The hide-books feature is disabled", 403)
    ub.session.add(ub.UserHiddenBook(user_id=int(current_user.id), book_id=int(book_id)))
    try:
        ub.session.commit()
    except Exception:
        ub.session.rollback()  # likely a dup/race; the row already exists
    return jsonify({"hidden": True})


@api_v1.route("/books/<int:book_id>/send", methods=["POST"])
@login_required_if_no_ano
def send_book_to_ereader(book_id):
    """Email a book to the user's e-reader (Kindle/Kobo), optionally converting.
    Body: {format, convert?: bool, emails?: "a@x,b@y"}. With no emails, sends to
    the user's configured kindle_mail. Reuses helper.send_mail."""
    guard = _require_real_user()
    if guard:
        return guard
    if not current_user.role_download():
        return _err("forbidden", "You don't have download permission", 403)
    if not config.get_mail_server_configured():
        return _err("mail_not_configured", "The server's email settings aren't configured", 400)

    data = request.get_json(silent=True) or {}
    book_format = (data.get("format") or "").strip().lower()
    if not book_format:
        return _err("invalid_request", "A book format is required", 400)
    convert = 1 if data.get("convert") else 0

    # Recipient: explicit list (validated) or the user's own kindle_mail.
    emails_raw = (data.get("emails") or "").strip()
    if emails_raw:
        try:
            recipients = valid_email(emails_raw)
        except Exception as ex:
            return _err("invalid_request", str(ex), 400)
    else:
        recipients = current_user.kindle_mail
        if not recipients:
            return _err("no_ereader_email",
                        "Add an e-reader email to your account first", 400)

    result = send_mail(book_id, book_format, convert, recipients, config.get_book_path(),
                       current_user.name, current_user.kindle_mail_subject)
    if result is None:
        ub.update_download(book_id, int(current_user.id))
        return jsonify({"ok": True, "message": "Book queued for sending to %s" % recipients})
    return _err("send_failed", "There was an error sending the book: %s" % result, 502)


@api_v1.route("/books/<int:book_id>/device-deliveries", methods=["POST"])
@login_required_if_no_ano
def queue_book_for_device(book_id):
    """Queue a pull delivery for one registered device owned by this user."""
    guard = _require_real_user()
    if guard:
        return guard
    if not current_user.role_download():
        return _err("forbidden", "You don't have download permission", 403)

    data = request.get_json(silent=True)
    public_id = data.get("device") if isinstance(data, dict) else None
    if not isinstance(public_id, str) or not public_id or len(public_id) > 36:
        return _err("invalid_request", "A device is required", 400)

    book = calibre_db.get_filtered_book(
        book_id, allow_show_archived=True, allow_show_hidden=True,
    )
    if book is None:
        return _err("not_found", "Book not found", 404)

    try:
        result = device_delivery.queue_book_for_device(
            session=ub.session,
            user_id=int(current_user.id),
            device_public_id=public_id,
            book=book,
        )
        ub.session.commit()
    except device_delivery.DeliveryValidationError as error:
        ub.session.rollback()
        return _err("device_not_found", str(error), 404)
    except Exception:
        ub.session.rollback()
        raise

    if result.reason == "already_on_device":
        return jsonify({
            "queued": False,
            "state": "already_on_device",
            "message": "This book is already on that device",
        })
    if result.reason == "insufficient_storage":
        return _err(
            "insufficient_storage",
            "This device does not have enough reported free space for this book",
            409,
        )
    if result.delivery.state == device_delivery.FAILED:
        return _err("no_readable_format", result.reason, 422)

    return jsonify({
        "delivery_id": result.delivery.id,
        "format": result.delivery.format,
        "queued": bool(result.created),
        "state": result.delivery.state,
        "message": (
            "Book queued for this device"
            if result.created else "This book is already queued for this device"
        ),
    })
