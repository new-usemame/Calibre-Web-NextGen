# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Book-upload endpoint for /api/v1.

Queues new book files into the CWA ingest folder using the same helpers the
legacy /upload route uses (_validate_uploaded_file, _get_ingest_path,
_save_to_ingest_atomic_rename, _ensure_ingest_dir_writable) — so validation,
atomic placement and the ingest hand-off stay single-sourced. The ingest
service then imports the files into the calibre library. Returns JSON with the
queued filenames and any per-file errors (the legacy route only flashed those).
"""
import os
import json

from flask import jsonify, request
from flask_babel import lazy_gettext as N_
from markupsafe import escape

from . import api_v1, log
from .. import config, calibre_db
from ..cw_login import current_user
from ..usermanagement import login_required_if_no_ano
from ..services.worker import WorkerThread
from ..tasks.upload import TaskUpload
from ..editbooks import (
    _validate_uploaded_file,
    _get_ingest_path,
    _save_to_ingest_atomic_rename,
    _ensure_ingest_dir_writable,
)


def _err(code, message, status):
    return jsonify({"error": {"code": code, "message": message}}), status


@api_v1.route("/upload", methods=["POST"])
@login_required_if_no_ano
def upload_books():
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not current_user.role_upload():
        return _err("forbidden", "You are not allowed to upload books", 403)

    files = [f for f in request.files.getlist("file") if f and f.filename]
    if not files:
        return _err("invalid_request", "No files were uploaded", 400)

    try:
        _ensure_ingest_dir_writable(allow_create=True, check_write=False)
    except PermissionError as e:
        log.error("Ingest directory not writable: %s", e)
        return _err("ingest_unwritable",
                    "The ingest folder is not writable; check the /cwa-book-ingest volume", 500)

    allowed = config.config_upload_formats
    queued, errors = [], []
    for uploaded in files:
        if not _validate_uploaded_file(uploaded):
            errors.append({"filename": uploaded.filename,
                           "error": "File type not allowed (allowed: {})".format(allowed)})
            continue
        try:
            final_path = _get_ingest_path(uploaded, prefix_parts=["new", current_user.id])
            tmp_path, final_path = _save_to_ingest_atomic_rename(uploaded, final_path)
            # The watched filename is deliberately prefixed for collision-free
            # staging. Carry the browser-selected basename explicitly so ingest
            # never has to guess which part of that internal name is user data.
            with open(final_path + ".cwa.json", "w", encoding="utf-8") as mf:
                json.dump({"action": "import", "original_filename": uploaded.filename},
                          mf, ensure_ascii=False)
            # The atomic rename into the watched ingest dir is what triggers import.
            os.replace(tmp_path, final_path)
            WorkerThread.add(current_user.name,
                             TaskUpload(N_("Upload done, processing, please wait..."),
                                        escape(uploaded.filename)))
            queued.append(uploaded.filename)
        except Exception as e:  # noqa: BLE001 — report per-file, keep going
            log.error_or_exception("Failed to queue upload for ingest: {}".format(e))
            errors.append({"filename": uploaded.filename, "error": "Failed to queue for processing"})

    return jsonify({"queued": queued, "errors": errors})


@api_v1.route("/books/<int:book_id>/formats", methods=["POST"])
@login_required_if_no_ano
def add_format(book_id):
    """Add a format (file) to an existing book. Mirrors the legacy
    do_edit_book btn-upload-format path: drop the file into the ingest folder
    with an ``add_format`` sidecar manifest; the ingest service attaches it to
    the book. Single-sourced via the same ingest helpers as /upload."""
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not current_user.role_upload():
        return _err("forbidden", "You are not allowed to upload books", 403)
    if not calibre_db.get_book(book_id):
        return _err("not_found", "Book not found", 404)

    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return _err("invalid_request", "No file was uploaded", 400)
    if not _validate_uploaded_file(uploaded):
        return _err("invalid_request",
                    "File type not allowed (allowed: {})".format(config.config_upload_formats), 400)

    try:
        _ensure_ingest_dir_writable(allow_create=True, check_write=False)
        final_path = _get_ingest_path(uploaded, prefix_parts=["format", book_id])
        tmp_path, final_path = _save_to_ingest_atomic_rename(uploaded, final_path)
        # Sidecar manifest tells the ingest service to attach this as a new
        # format on book_id rather than import it as a new book.
        with open(final_path + ".cwa.json", "w", encoding="utf-8") as mf:
            json.dump({"action": "add_format", "book_id": book_id,
                       "original_filename": uploaded.filename}, mf, ensure_ascii=False)
        os.replace(tmp_path, final_path)  # atomic move triggers ingest
        WorkerThread.add(current_user.name,
                         TaskUpload(N_("Upload done, processing, please wait..."),
                                    escape(uploaded.filename)))
    except PermissionError:
        return _err("ingest_unwritable",
                    "The ingest folder is not writable; check the /cwa-book-ingest volume", 500)
    except Exception as e:  # noqa: BLE001
        log.error_or_exception("Failed to queue format add: {}".format(e))
        return _err("server_error", "Failed to queue the format for processing", 500)

    return jsonify({"queued": uploaded.filename}), 202
