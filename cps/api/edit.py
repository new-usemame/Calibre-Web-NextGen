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
from ..helper import convert_book_format, save_cover, save_cover_from_url, tags_filters, get_convert_options

# Fields the SPA edit form can change, applied in this order. Title/authors come
# first because they may restructure the book's directory; the rest follow.
EDITABLE_FIELDS = [
    "title", "authors", "series", "series_index",
    "tags", "publishers", "languages", "comments", "rating", "pubdate",
]

# Custom columns (#pages, #status, …) are addressed by their calibre table name,
# the same key the classic editor's form fields and inline table editor use.
CUSTOM_COLUMN_PREFIX = "custom_column_"


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


def _custom_column_defs():
    """The custom columns the editor offers, or ``[]`` if they can't be read.

    ``filter_config_custom_read=False`` keeps the configured read-status column
    in the list, because the classic editor has always let you set it and
    dropping it here would be a silent parity loss. ``get_cc_columns`` already
    excludes the datatypes calibre itself can't edit (``db.cc_exceptions`` --
    composite and series) and degrades to ``[]`` on an unreadable metadata
    schema rather than taking the editor down over supplementary fields (#1153).
    """
    return calibre_db.get_cc_columns(config, filter_config_custom_read=False)


def _custom_column_value(book, column):
    """One custom column's current value as the string ``edit_cc_data`` parses.

    This is the inverse of the write path, so the shapes are not free choices:
    ``''`` means "no value", bool is ``'True'``/``'False'``, datetime is
    ``%Y-%m-%d`` (what ``edit_cc_data_value`` strptimes), rating is half-stars
    0-5 (``edit_cc_data_string`` multiplies by two on the way back), and a
    multi-value column is comma-joined (``edit_cc_data`` splits on ``,``).
    Anything that doesn't round trip corrupts the value on the next save.
    """
    rows = getattr(book, CUSTOM_COLUMN_PREFIX + str(column.id), None) or []
    if column.is_multiple:
        return ", ".join(str(row.value) for row in rows if row.value is not None)
    if not rows or rows[0].value is None:
        return ""
    value = rows[0].value
    if column.datatype == "bool":
        return "True" if value else "False"
    if column.datatype == "datetime":
        # Year <= 101 is calibre's DEFAULT_PUBDATE "no date" sentinel; render it
        # blank so the date input shows empty rather than 0101-01-01 (#689).
        if getattr(value, "year", 0) <= 101:
            return ""
        return value.date().isoformat() if hasattr(value, "date") else str(value)
    if column.datatype == "rating":
        # calibre stores 0-10; the editor speaks the same 0-5 half-stars the
        # classic form shows. Emitting the raw value doubles it on every save.
        return "%g" % (value / 2)
    return str(value)


def _custom_column_payload(book):
    """Definitions + current values for every editable custom column."""
    payload = []
    for column in _custom_column_defs():
        entry = {
            "id": column.id,
            "key": CUSTOM_COLUMN_PREFIX + str(column.id),
            "label": column.label,
            "name": column.name,
            "datatype": column.datatype,
            "is_multiple": bool(column.is_multiple),
            "value": _custom_column_value(book, column),
        }
        if column.datatype == "enumeration":
            # Allowed values live in the column's JSON `display` blob and were
            # only reachable through the classic /ajax/getcustomenum route, so
            # without them the SPA could only offer free text for a fixed set.
            try:
                entry["enum_values"] = list(column.get_display_dict().get("enum_values") or [])
            except (ValueError, TypeError, AttributeError):
                entry["enum_values"] = []
        payload.append(entry)
    return payload


def _custom_write_value(raw):
    """Normalize a JSON custom-column value to the string ``edit_cc_data`` wants.

    ``None`` clears (same as the classic form's empty field). Booleans are
    spelled out because ``edit_cc_data_value`` compares against the literal
    ``'True'``/``'False'`` -- ``str(False)`` happens to match, but a falsy
    collapse to ``''`` would clear the column instead of setting it to No.
    A list (how the SPA sends a multi-value column) is comma-joined.
    """
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return "True" if raw else "False"
    if isinstance(raw, (list, tuple)):
        return ", ".join(str(v) for v in raw if str(v).strip())
    return str(raw)


def _editable_metadata(book):
    """Current values for seeding the edit form (raw comments + rating included)."""
    comments = getattr(book, "comments", None) or []
    rating_rows = getattr(book, "ratings", None) or []
    languages = [
        isoLanguages.get_language_name(get_locale(), l.lang_code)
        for l in (getattr(book, "languages", None) or [])
    ]
    # Publication date — sentinel year <= 101 (Books.DEFAULT_PUBDATE) reads as
    # "" so the editor's <input type="date"> shows blank for "no pubdate"
    # (mirrors serialize_book_detail's null mapping, #689).
    pubdate_raw = getattr(book, "pubdate", None)
    pubdate = (
        pubdate_raw.date().isoformat()
        if pubdate_raw is not None and getattr(pubdate_raw, "year", 0) > 101
        else ""
    )
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
        # Publication date as YYYY-MM-DD (or "" for the default sentinel), #689.
        "pubdate": pubdate,
        # ISBN/ASIN/etc. — editable as a table in the SPA (fork #580).
        "identifiers": [
            {"type": i.type, "val": i.val}
            for i in (getattr(book, "identifiers", None) or [])
        ],
        # User-defined calibre columns (#pages, #status, …). The SPA has shown
        # these on the book page since v4.1.11 but had no way to change one, so
        # setting a page count meant switching back to the classic view (#997).
        "custom_columns": _custom_column_payload(book),
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


# Editor typeahead: existing-value suggestions per metadata field, so the SPA
# editor stops spawning near-duplicate tags/series/authors from typos
# (#741, #778, #689). Each field maps to the calibre model + name-normalization
# the legacy /get_*_json routes use; reusing calibre_db.get_typeahead keeps the
# SPA's suggestions identical to the classic editor's (single source of truth).
_TYPEAHEAD_MODELS = {
    "authors": (lambda: db.Authors, ("|", ",")),
    "publishers": (lambda: db.Publishers, ("|", ",")),
    "series": (lambda: db.Series, ("", "")),
}
_TYPEAHEAD_LIMIT = 25


def _typeahead_names(field, query):
    """Suggestion names for one editor metadata field, or None for an unknown
    field. Mirrors cps/web.py's typeahead routes field-for-field."""
    query = query or ""
    if field == "tags":
        raw = calibre_db.get_typeahead(db.Tags, query, tag_filter=tags_filters())
        return [row["name"] for row in json.loads(raw)][:_TYPEAHEAD_LIMIT]
    if field == "languages":
        # Localized display names, ranked start-matches-first (same as
        # /get_languages_json) but with the editor's larger result cap.
        needle = query.lower()
        names = list(isoLanguages.get_language_names(get_locale()).values())
        ranked = [s for s in names if s.lower().startswith(needle)]
        if len(ranked) < _TYPEAHEAD_LIMIT:
            seen = set(ranked)
            ranked.extend(s for s in names if needle in s.lower() and s not in seen)
        # de-dupe preserving the start-first order
        return list(dict.fromkeys(ranked))[:_TYPEAHEAD_LIMIT]
    spec = _TYPEAHEAD_MODELS.get(field)
    if spec is None:
        return None
    model_factory, replace = spec
    raw = calibre_db.get_typeahead(model_factory(), query, replace)
    return [row["name"] for row in json.loads(raw)][:_TYPEAHEAD_LIMIT]


@api_v1.route("/metadata/typeahead/<field>")
@login_required_if_no_ano
def metadata_typeahead(field):
    """Existing-value suggestions for an editor metadata field — one of tags,
    authors, series, publishers, languages. Read-only; gated on the edit role
    (only editors open the editor, and it avoids leaking the library's full
    tag/author list to non-editors). Reuses the legacy typeahead query so both
    editors agree on what already exists (#741, #778, #689)."""
    guard = _require_edit()
    if guard:
        return guard
    names = _typeahead_names(field, request.args.get("q", ""))
    if names is None:
        return _err("invalid_request", "Unknown typeahead field", 400)
    return jsonify({"field": field, "suggestions": names})


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

    # Custom columns (#997). edit_book_param already has a custom_column_ branch
    # feeding edit_single_cc_data — the core the classic inline table editor has
    # always used — so this is a routing gap, not missing write support. The id
    # is checked against the real definitions first: edit_single_cc_data filters
    # by id and simply does nothing when it matches no column, which would make
    # a bad key look like a successful save.
    custom_keys = [k for k in data if isinstance(k, str) and k.startswith(CUSTOM_COLUMN_PREFIX)]
    if custom_keys:
        editable = {CUSTOM_COLUMN_PREFIX + str(c.id) for c in _custom_column_defs()}
        for key in custom_keys:
            if key not in editable:
                errors[key] = "Unknown or non-editable custom column"
                continue
            vals = {"pk": str(book_id), "value": _custom_write_value(data[key])}
            ok, message = _parse_edit_result(edit_book_param(key, vals))
            if not ok:
                errors[key] = message

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
    # Authorize against the caller's VISIBLE library, not the raw table: a user
    # with the (global) delete role but a language/tag/custom-column visibility
    # restriction must not be able to enumerate and delete a book they cannot
    # see. allow_show_archived/hidden keep their OWN archived/hidden books
    # deletable (hidden is a listing exclusion, not an access revocation — #319).
    if not calibre_db.get_filtered_book(book_id, allow_show_archived=True, allow_show_hidden=True):
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
    # Same visibility-scoped authorization as whole-book delete above.
    if not calibre_db.get_filtered_book(book_id, allow_show_archived=True, allow_show_hidden=True):
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
    book = calibre_db.get_book(book_id)
    if not book:
        return _err("not_found", "Book not found", 404)
    data = request.get_json(silent=True) or {}
    src = (data.get("from") or "").strip().upper()
    dst = (data.get("to") or "").strip().upper()
    if not src or not dst:
        return _err("invalid_request", "Source and target formats are required", 400)
    if src == dst:
        return _err("invalid_request", "Source and target formats are the same", 400)
    allowed_sources, allowed_targets = get_convert_options(book)
    allowed_sources = [f.upper() for f in allowed_sources]
    allowed_targets = [f.upper() for f in allowed_targets]
    if src not in allowed_sources:
        return _err("invalid_request", "Source format is not valid for conversion", 400)
    if dst not in allowed_targets:
        return _err("invalid_request", "Target format is not valid for conversion", 400)
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
