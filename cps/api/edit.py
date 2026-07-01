# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Edit-metadata endpoints for /api/v1.

Reuses cps/editbooks.py's edit_book_param — the canonical single-field editor
behind the legacy inline books-table editor — so every metadata write goes
through the same logic (directory restructuring on title/author change, tag/
series/language parsing, activity logging, commit/rollback). The SPA edit form
presents all fields together; we apply each changed field through that core.
"""
import json

from flask import jsonify, request, Response
from flask_babel import get_locale

from . import api_v1
from .serializers import serialize_book_detail
from .. import calibre_db, config, db, ub, isoLanguages
from ..cw_login import current_user
from ..usermanagement import login_required_if_no_ano
import time

from ..editbooks import edit_book_param, delete_book_from_table, modify_identifiers
from ..helper import convert_book_format, save_cover, save_cover_from_url

# Fields the SPA edit form can change, applied in this order. Title/authors come
# first because they may restructure the book's directory; the rest follow.
EDITABLE_FIELDS = [
    "title", "authors", "series", "series_index",
    "tags", "publishers", "languages", "comments", "rating",
]


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


def _require_edit():
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not current_user.role_edit():
        return _err("forbidden", "You are not allowed to edit metadata", 403)
    return None


def _parse_edit_result(result):
    """edit_book_param returns a JSON Response, an empty string, or a
    ``(message, status)`` tuple. Normalize to ``(ok: bool, message: str)``."""
    if isinstance(result, Response):
        try:
            payload = json.loads(result.get_data(as_text=True) or "{}")
        except ValueError:
            return True, ""  # non-JSON success body
        if payload.get("success") is False:
            return False, payload.get("msg", "Update failed")
        return True, ""
    if isinstance(result, tuple):  # (message, status) — an error
        return False, str(result[0])
    return True, ""  # "" / None — success with no body


def _editable_metadata(book):
    """Current values for seeding the edit form (raw comments + rating included)."""
    comments = getattr(book, "comments", None) or []
    rating_rows = getattr(book, "ratings", None) or []
    languages = [
        isoLanguages.get_language_name(get_locale(), l.lang_code)
        for l in (getattr(book, "languages", None) or [])
    ]
    return {
        "id": book.id,
        "title": book.title or "",
        # calibre stores authors '|'-joined internally; present them '&'-joined
        # (the format edit_book_param's author handler expects back).
        "authors": " & ".join(a.name.replace("|", ",") for a in (book.authors or [])),
        "series": book.series[0].name if getattr(book, "series", None) else "",
        "series_index": book.series_index,
        "tags": ", ".join(t.name for t in (getattr(book, "tags", None) or [])),
        "publishers": ", ".join(p.name for p in (getattr(book, "publishers", None) or [])),
        "languages": ", ".join(languages),
        "comments": comments[0].text if comments else "",
        # calibre ratings are stored 0-10 (half-stars); expose 0-5.
        "rating": (rating_rows[0].rating / 2) if rating_rows else 0,
        # ISBN/ASIN/etc. — editable as a table in the SPA (fork #580).
        "identifiers": [
            {"type": i.type, "val": i.val}
            for i in (getattr(book, "identifiers", None) or [])
        ],
    }


@api_v1.route("/books/<int:book_id>/metadata")
@login_required_if_no_ano
def get_metadata(book_id):
    guard = _require_edit()
    if guard:
        return guard
    book = calibre_db.get_book(book_id)
    if not book:
        return _err("not_found", "Book not found", 404)
    return jsonify(_editable_metadata(book))


@api_v1.route("/books/<int:book_id>/metadata", methods=["POST"])
@login_required_if_no_ano
def update_metadata(book_id):
    guard = _require_edit()
    if guard:
        return guard
    book = calibre_db.get_book(book_id)
    if not book:
        return _err("not_found", "Book not found", 404)

    data = request.get_json(silent=True) or {}
    errors = {}
    for field in EDITABLE_FIELDS:
        if field not in data:
            continue
        raw = data[field]
        value = "" if raw is None else str(raw)
        # edit_book_param reads vals['pk'] + vals['value']; checkA auto-syncs the
        # author sort key from the authors string (the inline-editor default).
        vals = {"pk": str(book_id), "value": value, "checkA": "true"}
        ok, message = _parse_edit_result(edit_book_param(field, vals))
        if not ok:
            errors[field] = message

    # Identifiers (ISBN/ASIN/…) — a list of {type, val}, reconciled against the
    # book's existing rows via the same helper the legacy editor uses (fork #580).
    if "identifiers" in data:
        raw_ids = data.get("identifiers") or []
        input_identifiers = []
        for entry in raw_ids if isinstance(raw_ids, list) else []:
            if not isinstance(entry, dict):
                continue
            id_type = str(entry.get("type") or "").strip().lower()
            id_val = str(entry.get("val") or "").strip()
            if not id_type or not id_val:
                continue  # skip blank rows (a half-filled row isn't an error)
            input_identifiers.append(db.Identifiers(id_val, id_type, book_id))
        changed, id_error = modify_identifiers(
            input_identifiers, book.identifiers, calibre_db.session)
        if id_error:
            # A duplicate type may have already queued partial add/deletes on the
            # session — discard them so a rejected payload never persists partially.
            calibre_db.session.rollback()
            errors["identifiers"] = "Duplicate identifier type — each type may appear once."
        elif changed:
            try:
                calibre_db.session.commit()
            except Exception as exc:  # noqa: BLE001 — surface as a field error, don't 500
                calibre_db.session.rollback()
                errors["identifiers"] = str(exc)

    # Re-fetch so the response reflects the committed state.
    fresh = calibre_db.get_book(book_id)
    body = _editable_metadata(fresh) if fresh else {}
    if errors:
        body["errors"] = errors
    return jsonify(body)


@api_v1.route("/books/<int:book_id>/delete", methods=["POST"])
@login_required_if_no_ano
def delete_book(book_id):
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not current_user.role_delete_books():
        return _err("forbidden", "You are not allowed to delete books", 403)
    if not calibre_db.get_book(book_id):
        return _err("not_found", "Book not found", 404)
    # delete_book_from_table re-checks the role and does the data-safe (DB-first,
    # files-last) whole-book delete + shelf cleanup. book_format="" = whole book.
    delete_book_from_table(book_id, "", True)
    return "", 204


@api_v1.route("/books/<int:book_id>/formats/<fmt>/delete", methods=["POST"])
@login_required_if_no_ano
def delete_format(book_id, fmt):
    """Delete a single format from a book (keeps the book). Reuses the data-safe
    delete core (re-checks role, DB-first/files-last)."""
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not current_user.role_delete_books():
        return _err("forbidden", "You are not allowed to delete books", 403)
    if not calibre_db.get_book(book_id):
        return _err("not_found", "Book not found", 404)
    delete_book_from_table(book_id, fmt.upper(), True)
    return "", 204


@api_v1.route("/books/<int:book_id>/convert", methods=["POST"])
@login_required_if_no_ano
def convert_format(book_id):
    """Queue a format conversion. Body: {from, to}. Reuses helper.convert_book_format."""
    guard = _require_edit()
    if guard:
        return guard
    if not calibre_db.get_book(book_id):
        return _err("not_found", "Book not found", 404)
    data = request.get_json(silent=True) or {}
    src = (data.get("from") or "").strip().upper()
    dst = (data.get("to") or "").strip().upper()
    if not src or not dst:
        return _err("invalid_request", "Source and target formats are required", 400)
    if src == dst:
        return _err("invalid_request", "Source and target formats are the same", 400)
    rtn = convert_book_format(book_id, config.get_book_path(), src, dst, current_user.name)
    if rtn is None:
        return jsonify({"ok": True, "message": "Queued for conversion to %s" % dst})
    return _err("convert_failed", "There was an error converting this book: %s" % rtn, 400)


@api_v1.route("/books/<int:book_id>/cover", methods=["POST"])
@login_required_if_no_ano
def set_cover(book_id):
    """Replace a book's cover from an uploaded image (multipart `file`) or a
    remote URL (JSON {url}). Reuses helper.save_cover / save_cover_from_url so the
    size/format checks match the legacy edit page. The dedicated cover *picker*
    (provider candidate grid, e-reader padding preview) stays at /book/<id>/cover."""
    guard = _require_edit()
    if guard:
        return guard
    book = calibre_db.get_filtered_book(book_id)
    if not book:
        return _err("not_found", "Book not found", 404)

    if request.files.get("file"):
        ok, message = save_cover(request.files["file"], book.path)
    else:
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        if not url:
            return _err("invalid_request", "Provide an image file or a cover URL", 400)
        ok, message = save_cover_from_url(url, book.path)

    if ok:
        # Cache-bust so the browser refetches the replaced image immediately.
        return jsonify({"ok": True, "cover_url": "/cover/%d/og?t=%d" % (book_id, int(time.time()))})
    return _err("cover_failed", str(message), 400)
