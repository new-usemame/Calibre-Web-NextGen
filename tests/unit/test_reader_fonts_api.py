# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral API boundaries for uploaded reader font delivery and mutations."""
import inspect
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
import uuid

import flask
import pytest


def _ctx(path, method="GET", **kwargs):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    return app.test_request_context(path, method=method, **kwargs)


def test_font_asset_is_same_origin_and_private_cached():
    from cps.api import reader_fonts as mod

    font_id = uuid.uuid4()
    record = {"id": str(font_id), "extension": ".woff2", "path": "ignored"}
    handle = BytesIO(b"validated-font")
    with _ctx("/api/v1/reader/fonts/%s/file" % font_id):
        with patch.object(mod.reader_fonts, "open_file_for_font", return_value=(record, handle)):
            response = inspect.unwrap(mod.reader_font_file)(font_id)
            response.direct_passthrough = False
            assert response.get_data() == b"validated-font"
            assert response.headers["Cache-Control"] == "private, max-age=31536000, immutable"
            assert response.headers["Cross-Origin-Resource-Policy"] == "same-origin"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            response.close()
    assert handle.closed


def test_non_admin_cannot_upload_fonts():
    from cps.api import reader_fonts as mod

    with _ctx("/api/v1/admin/reader/fonts", method="POST"):
        with patch.object(mod, "current_user", SimpleNamespace(
            is_authenticated=True, is_anonymous=False, role_admin=lambda: False,
        )), patch.object(mod.reader_fonts, "upload_font") as upload:
            response, status = inspect.unwrap(mod.admin_upload_reader_font)()

    assert status == 403
    assert response.get_json()["error"]["code"] == "forbidden"
    upload.assert_not_called()


def test_upload_response_does_not_reread_catalog_after_publish():
    from cps.api import reader_fonts as mod

    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    app.add_url_rule(
        "/api/v1/reader/fonts/<uuid:font_uuid>/file",
        endpoint="api_v1.reader_font_file",
        view_func=lambda font_uuid: None,
    )
    font_id = uuid.uuid4()
    record = {
        "id": str(font_id), "extension": ".ttf", "label": "Example",
        "family": "CWNGUpload_" + font_id.hex,
    }
    expected_option = {"id": "custom:" + str(font_id), "label": "Example"}
    with app.test_request_context(
        "/api/v1/admin/reader/fonts", method="POST",
        data={"file": (BytesIO(b"font data"), "font.ttf")},
        content_type="multipart/form-data",
    ):
        with patch.object(mod, "_require_admin", return_value=None), \
             patch.object(mod.reader_fonts, "upload_font", return_value=(record, True)), \
             patch.object(mod.reader_fonts, "font_option", return_value=expected_option) as option, \
             patch.object(mod.reader_fonts, "catalogue", side_effect=AssertionError("catalogue raced with delete")):
            response, status = inspect.unwrap(mod.admin_upload_reader_font)()

    assert status == 201
    assert response.get_json() == {"item": expected_option, "created": True}
    option.assert_called_once()


def test_oversized_multipart_is_rejected_before_csrf_parses_form():
    """The route-specific cap must run before Flask-WTF inspects request.form."""
    import cps
    from cps.services import reader_fonts

    app = flask.Flask(__name__)
    app.secret_key = "test"
    cps._register_reader_font_upload_limiter(app)
    if cps.csrf:
        cps.csrf.init_app(app)
    called = []
    app.add_url_rule(
        "/api/v1/admin/reader/fonts", methods=["POST"],
        endpoint="api_v1.admin_upload_reader_font",
        view_func=lambda: called.append(True) or "unexpected route call",
    )

    response = app.test_client().post(
        "/api/v1/admin/reader/fonts",
        data={"file": (BytesIO(b"x" * (9 * 1024 * 1024)), "large.ttf")},
        content_type="multipart/form-data",
        headers={"X-CSRFToken": "test-token"},
    )

    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "font_too_large"
    assert not called


def test_upload_stream_without_content_length_is_bounded_during_csrf_parsing():
    """Chunked WSGI input must still hit the request-local form-parser limit."""
    import cps
    from werkzeug.test import EnvironBuilder

    app = flask.Flask(__name__)
    app.secret_key = "test"
    cps._register_reader_font_upload_limiter(app)
    if cps.csrf:
        cps.csrf.init_app(app)
    called = []
    app.add_url_rule(
        "/api/v1/admin/reader/fonts", methods=["POST"],
        endpoint="api_v1.admin_upload_reader_font",
        view_func=lambda: called.append(True) or "unexpected route call",
    )

    boundary = "----reader-font-limit-probe"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"large.ttf\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
        + b"x" * (9 * 1024 * 1024)
        + f"\r\n--{boundary}--\r\n".encode()
    )
    builder = EnvironBuilder(
        path="/api/v1/admin/reader/fonts", method="POST", input_stream=BytesIO(body),
        content_type=f"multipart/form-data; boundary={boundary}",
        content_length=None, headers={"X-CSRFToken": "test-token"},
    )
    environ = builder.get_environ()
    environ.pop("CONTENT_LENGTH", None)
    environ["wsgi.input_terminated"] = True
    environ["HTTP_TRANSFER_ENCODING"] = "chunked"
    captured = {}

    def start_response(status, _headers, _exc_info=None):
        captured["status"] = status

    response = b"".join(app.wsgi_app(environ, start_response))

    assert captured["status"].startswith("413 ")
    assert b"Request Entity Too Large" in response
    assert not called
