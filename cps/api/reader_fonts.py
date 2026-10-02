# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reader-font catalogue and administrator upload endpoints."""
from __future__ import annotations

import uuid

from flask import jsonify, request, send_file, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from . import api_v1
from .. import logger
from ..cw_login import current_user
from ..services import reader_fonts
from ..usermanagement import login_required_if_no_ano

log = logger.create()


def _err(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


def _require_admin():
    if not current_user.is_authenticated or current_user.is_anonymous:
        return _err("unauthorized", "You must be signed in", 401)
    if not current_user.role_admin():
        return _err("forbidden", "Admin access required", 403)
    return None


def _font_file_url(font_uuid: uuid.UUID) -> str:
    return url_for("api_v1.reader_font_file", font_uuid=font_uuid)


@api_v1.route("/reader/fonts")
@login_required_if_no_ano
def reader_font_catalogue():
    """Built-in and instance-wide custom fonts available to this reader."""
    response = jsonify(reader_fonts.catalogue(_font_file_url))
    response.headers["Cache-Control"] = "no-cache"
    return response


@api_v1.route("/reader/fonts/<uuid:font_uuid>/file")
@login_required_if_no_ano
def reader_font_file(font_uuid):
    """Serve a validated instance-wide font to classic and SPA EPUB frames."""
    opened = reader_fonts.open_file_for_font(font_uuid)
    if not opened:
        return _err("font_not_found", "Reader font not found", 404)
    record, font_handle = opened
    fmt = reader_fonts.FONT_FORMATS[record["extension"]]
    try:
        response = send_file(
            font_handle,
            mimetype=fmt["mime"],
            as_attachment=False,
            download_name="reader-font" + record["extension"],
            conditional=False,
            max_age=31536000,
        )
    except Exception:
        font_handle.close()
        raise
    response.call_on_close(font_handle.close)
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Content-Disposition"] = "inline"
    return response


@api_v1.route("/admin/reader/fonts", methods=["POST"])
@login_required_if_no_ano
def admin_upload_reader_font():
    guard = _require_admin()
    if guard:
        return guard
    # Include a small multipart envelope allowance while bounding the request
    # parser itself; upload_font applies the stricter byte limit to the file.
    max_request_bytes = reader_fonts.MAX_FONT_FILE_BYTES + 256 * 1024
    request.max_content_length = max_request_bytes
    if request.content_length is not None and request.content_length > max_request_bytes:
        return _err("font_too_large", "Font upload exceeds the 8 MiB limit", 413)
    try:
        uploaded = request.files.get("file")
    except RequestEntityTooLarge:
        return _err("font_too_large", "Font upload exceeds the 8 MiB limit", 413)
    if not uploaded or not uploaded.filename:
        return _err("invalid_request", "Choose a font file to upload", 400)
    try:
        record, created = reader_fonts.upload_font(
            uploaded.stream,
            uploaded.filename,
            request.form.get("name") or request.form.get("label"),
        )
    except reader_fonts.ReaderFontError as exc:
        status = 413 if "larger than" in str(exc) else 400
        if "limit has been reached" in str(exc) or "storage limit" in str(exc):
            status = 409
        return _err("invalid_font", str(exc), status)
    except reader_fonts.ReaderFontValidatorUnavailable as exc:
        log.error("Reader font validation unavailable: %s", exc)
        return _err("font_validation_unavailable",
                    "The server cannot safely validate uploaded fonts right now", 503)
    # Build from the record returned by the successful atomic publish. A
    # concurrent admin delete may remove it from the catalogue before this
    # response is serialized; that must not turn a completed upload into 500.
    option = reader_fonts.font_option(record, _font_file_url)
    return jsonify({"item": option, "created": created}), (201 if created else 200)


@api_v1.route("/admin/reader/fonts/<uuid:font_uuid>", methods=["DELETE"])
@login_required_if_no_ano
def admin_delete_reader_font(font_uuid):
    guard = _require_admin()
    if guard:
        return guard
    if not reader_fonts.delete_font(font_uuid):
        return _err("font_not_found", "Reader font not found", 404)
    return jsonify({"deleted": str(font_uuid)})
