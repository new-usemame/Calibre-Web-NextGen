# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""KOReader library routes: manifest, placeholders, book files, read status.

All live under ``/kosync`` with the same HTTP Basic auth (account password or
app password) and the same admin switch as progress sync. What the manifest
means is documented in ``cps/services/koreader_library.py``.

    GET /kosync/syncs/library                          the manifest, paged
    GET /kosync/syncs/library/books/<id>/placeholder   placeholder EPUB
    GET /kosync/syncs/library/books/<id>/file          the book itself
    PUT /kosync/syncs/read_status                      explicit read status
"""

import hashlib
import os
from urllib.parse import quote

from flask import g, jsonify, make_response, request, send_from_directory
from flask_babel import gettext as _
from sqlalchemy.exc import SQLAlchemyError

from ... import config, csrf, logger, ub
from ...services import koreader_library, koreader_placeholder
from .kosync import (
    MAX_DEVICE_ID_LENGTH,
    MAX_DEVICE_LENGTH,
    MAX_DOCUMENT_LENGTH,
    _is_ascii_book_id,
    _require_kosync_enabled,
    authenticate_user,
    get_book_by_checksum,
    is_valid_field,
    kosync,
)

log = logger.create()

_READ_STATUSES = (koreader_library.READ_UNREAD, koreader_library.READ_READING,
                  koreader_library.READ_FINISHED)


def _error(message, status, error):
    return jsonify({"error": error, "message": message}), status


def _device_user():
    """Authenticate the device and act as its user for the rest of the request.

    Magic-shelf rules, visibility checks and read-status writes all resolve
    ``current_user``; Basic auth alone does not log anyone in, so the request
    adopts the authenticated account the same way OPDS Basic auth does.
    """
    user = authenticate_user()
    if user is None:
        return None
    g.flask_httpauth_user = user
    try:
        from flask_babel import refresh
        refresh()
    except Exception:  # no Babel on a bare test app: nothing cached to refresh
        pass
    return user


def _device_identity(body=None):
    """``(name, id)`` from the JSON body, headers or query; ``None`` if absent."""
    body = body if isinstance(body, dict) else {}
    name = (body.get("device") or request.headers.get("X-CWNG-Device-Name")
            or request.args.get("device"))
    raw_id = (body.get("device_id") or request.headers.get("X-CWNG-Device-ID")
              or request.args.get("device_id"))
    if (not is_valid_field(name) or len(name) > MAX_DEVICE_LENGTH
            or not is_valid_field(raw_id) or len(raw_id) > MAX_DEVICE_ID_LENGTH):
        return None
    return name, raw_id


def _observe_device(user, body=None):
    """Register the KOReader device behind this request, never failing it."""
    identity = _device_identity(body)
    if identity is None:
        return
    try:
        from ...services.device_registry import register_koreader_device_best_effort
        register_koreader_device_best_effort(
            user_id=user.id, device_id=identity[1], device_name=identity[0])
    except Exception:
        log.warning("Best-effort KOReader device registration from the library "
                    "failed", exc_info=True)


def _library_root():
    """The local library folder, or ``None`` when books live on Google Drive."""
    if bool(getattr(config, "config_use_google_drive", False)):
        return None
    return config.get_book_path()


def _visible_book(user, book_id):
    from ... import calibre_db
    # A reader can always reach their own hidden or archived book; everything
    # else (content restrictions, My Library) applies as on the website.
    return calibre_db.get_filtered_book(
        book_id, allow_show_archived=True, allow_show_hidden=True, user=user)


def _page_limit(raw):
    if raw in (None, ""):
        return koreader_library.DEFAULT_PAGE_SIZE
    if not raw.isascii() or not raw.isdigit():
        raise ValueError("limit must be a positive integer")
    return max(1, min(int(raw), koreader_library.MAX_PAGE_SIZE))


@csrf.exempt
@kosync.route("/kosync/syncs/library", methods=["GET"])
def get_library():
    """Every book this account's e-readers should hold, one page at a time."""
    blocked = _require_kosync_enabled()
    if blocked:
        return blocked
    user = _device_user()
    if user is None:
        return _error("Unauthorized", 401, "unauthorized")
    if not user.role_download():
        return _error("Download permission is required", 403, "forbidden")
    try:
        limit = _page_limit(request.args.get("limit"))
        cursor = request.args.get("cursor") or None
        cursor_revision, after = (koreader_library.decode_cursor(cursor)
                                  if cursor else (None, None))
    except ValueError as error:
        return _error(str(error), 400, "invalid_request")
    _observe_device(user)
    # The pages after the first come from the manifest this sync started with
    # (see koreader_library's "Syncs still reading pages"); a sync the server
    # no longer remembers carries on from the library as it is now.
    manifest = (koreader_library.walk_manifest(user.id, cursor_revision)
                if cursor_revision else None)
    try:
        if manifest is None:
            manifest = koreader_library.build_manifest(user)
    except koreader_library.ScopeUnavailable as error:
        # Never serve a short list: the device would treat the missing books
        # as removed from its library (the #468 lesson, applied here).
        log.warning("KOReader library for %s not served: %s", user.name, error)
        return _error(str(error), 503, "scope_unavailable")
    except SQLAlchemyError:
        ub.session.rollback()
        log.error("KOReader library manifest failed", exc_info=True)
        return _error("The library is unavailable right now", 503,
                      "library_unavailable")

    if_revision = request.args.get("if_revision")
    if cursor is None and if_revision and if_revision == manifest.revision:
        response = jsonify({"unchanged": True, "revision": manifest.revision})
        response.headers["Cache-Control"] = "private, no-store"
        return response

    # A walk keeps the revision it started with, so a change that lands
    # between two pages is picked up by the next sync instead of hidden.
    revision = cursor_revision or manifest.revision
    books, more = koreader_library.page(manifest.entries, after=after, limit=limit)
    koreader_library.continue_walk(user.id, revision, manifest, more=bool(more and books))
    # File facts are read fresh for every page; the kept manifest stays as built.
    books = [dict(book) for book in books]
    koreader_library.fill_file_facts(books, manifest.paths, _library_root())
    response = jsonify({
        "revision": revision,
        "scope": manifest.scope,
        "scope_shelves": manifest.scope_shelves,
        "shelves": manifest.shelves,
        "total": len(manifest.entries),
        "unsupported": manifest.unsupported,
        "next_cursor": (koreader_library.encode_cursor(revision, books[-1]["book_id"])
                        if more and books else None),
        "books": books,
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _cover_path(book):
    root = _library_root()
    if root is None or not getattr(book, "has_cover", False) or not book.path:
        return None
    path = koreader_library.inside_library(root, book.path, "cover.jpg")
    return path if path and os.path.isfile(path) else None


def _placeholder_language():
    try:
        from flask_babel import get_locale
        locale = get_locale()
        return str(locale.language) if locale else "en"
    except Exception:
        return "en"


@csrf.exempt
@kosync.route("/kosync/syncs/library/books/<int:book_id>/placeholder",
              methods=["GET"])
def get_library_placeholder(book_id):
    """The small EPUB that shows a not-yet-downloaded book in the grid."""
    blocked = _require_kosync_enabled()
    if blocked:
        return blocked
    user = _device_user()
    if user is None:
        return _error("Unauthorized", 401, "unauthorized")
    book = _visible_book(user, book_id)
    if book is None:
        return _error("Book not found", 404, "not_found")
    entry, _file = koreader_library.describe_book(user, book_id)
    if entry is None:
        return _error("This book has no format KOReader can read", 404,
                      "no_readable_format")

    language = _placeholder_language()
    etag = hashlib.sha256(("%s|%s|%s" % (book_id, entry["rev"], language))
                          .encode("utf-8")).hexdigest()[:32]
    if etag in request.if_none_match:
        response = make_response("", 304)
        response.set_etag(etag)
        return response

    data = koreader_placeholder.cached(etag, lambda: koreader_placeholder.build(
        book_id=book_id,
        rev=entry["rev"],
        title=entry["title"],
        authors=entry["authors"],
        series=entry["series"],
        series_index=entry["series_index"],
        cover_path=_cover_path(book),
        language=language,
        modified=koreader_library.iso_z(book.last_modified) or "2000-01-01T00:00:00Z",
        heading=_("Not on this device yet"),
        lines=[
            _("This book is in your library but has not been downloaded to this device."),
            _("Connect to Wi-Fi and open it from your library: it downloads by itself."),
        ],
    ))
    response = make_response(data)
    response.headers["Content-Type"] = koreader_placeholder.MIMETYPE
    response.headers["X-CWNG-Placeholder-Rev"] = entry["rev"]
    response.headers["Cache-Control"] = "private, no-cache"
    response.set_etag(etag)
    return response


@csrf.exempt
@kosync.route("/kosync/syncs/library/books/<int:book_id>/file", methods=["GET"])
def get_library_file(book_id):
    """The book itself, in the format KOReader reads best."""
    blocked = _require_kosync_enabled()
    if blocked:
        return blocked
    user = _device_user()
    if user is None:
        return _error("Unauthorized", 401, "unauthorized")
    if not user.role_download():
        return _error("Download permission is required", 403, "forbidden")
    book = _visible_book(user, book_id)
    if book is None:
        return _error("Book not found", 404, "not_found")
    entry, (book_path, fmt_row) = koreader_library.describe_book(user, book_id)
    if entry is None:
        return _error("This book has no format KOReader can read", 404,
                      "no_readable_format")
    _observe_device(user)
    filename = entry["filename"]
    library_filename = "%s.%s" % (fmt_row.name, fmt_row.format.lower())

    root = _library_root()
    if root is None:
        from ... import gdriveutils as gd
        remote = gd.getFileFromEbooksFolder(book.path, library_filename)
        if remote is None:
            return _error("The book file is missing from the library", 404,
                          "file_missing")
        response = gd.do_gdrive_download(remote, {
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename),
            "Content-Type": "application/octet-stream",
            "X-CWNG-Filename": quote(filename),
        })
        _record_download(user, book_id)
        return response

    path = koreader_library.library_file_path(root, book_path, fmt_row)
    if path is None or not os.path.isfile(path):
        return _error("The book file is missing from the library", 404,
                      "file_missing")
    _size, checksum = koreader_library.file_facts(path)
    _register_checksum(book_id, fmt_row.format, path, filename)
    _record_download(user, book_id)
    response = send_from_directory(os.path.dirname(path), os.path.basename(path),
                                   as_attachment=True, download_name=filename)
    if checksum:
        response.headers["X-CWNG-Checksum"] = checksum
    response.headers["X-CWNG-Filename"] = quote(filename)
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _register_checksum(book_id, book_format, path, filename):
    """Let progress from this copy find its book (binary and filename digests)."""
    try:
        from ..checksums import calculate_and_store_checksum
        calculate_and_store_checksum(book_id, book_format, path,
                                     filename_for_matching=filename)
    except Exception:
        log.warning("Could not register the KOReader checksum of book %s",
                    book_id, exc_info=True)


def _record_download(user, book_id):
    try:
        ub.update_download(book_id, int(user.id))
    except Exception:
        ub.session.rollback()
        log.debug("Could not record the download of book %s", book_id,
                  exc_info=True)


def _resolve_book_id(data):
    book_id = data.get("book_id")
    if book_id is not None:
        if isinstance(book_id, bool) or not isinstance(book_id, int) or book_id <= 0:
            raise ValueError("book_id must be a positive integer")
        return book_id
    document = data.get("document")
    if not is_valid_field(document) or len(document) > MAX_DOCUMENT_LENGTH:
        raise ValueError("book_id or document is required")
    if _is_ascii_book_id(document):
        return int(document)
    found, _format, _title, _path, _version = get_book_by_checksum(document)
    return found


@csrf.exempt
@kosync.route("/kosync/syncs/read_status", methods=["PUT"])
def put_read_status():
    """A status the reader chose on the device: unread, reading or finished."""
    blocked = _require_kosync_enabled()
    if blocked:
        return blocked
    user = _device_user()
    if user is None:
        return _error("Unauthorized", 401, "unauthorized")
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("A JSON object is required", 400, "invalid_request")
    if _device_identity(data) is None:
        return _error("device and device_id are required", 400, "invalid_request")
    status = data.get("status")
    if status not in _READ_STATUSES:
        return _error("status must be unread, reading or finished", 400,
                      "invalid_request")
    try:
        book_id = _resolve_book_id(data)
    except ValueError as error:
        return _error(str(error), 400, "invalid_request")
    if book_id is None or _visible_book(user, book_id) is None:
        return _error("Book not found", 404, "not_found")
    _observe_device(user, data)
    try:
        problem = koreader_library.set_read_status(user, book_id, status)
    except SQLAlchemyError:
        ub.session.rollback()
        log.error("KOReader read status for book %s failed", book_id, exc_info=True)
        return _error("Read status could not be saved", 503, "unavailable")
    if problem:
        # The problem text can carry the database's own words (SQL, table
        # names): the admin reads it in the log, the device gets a plain answer.
        log.error("KOReader read status for book %s not saved: %s", book_id, problem)
        return _error("Read status could not be saved", 500, "read_status_failed")
    return jsonify({"book_id": book_id, "read_status": status})
