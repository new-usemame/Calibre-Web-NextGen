# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for /api/v1 upload — role/auth gating and per-file queue/error
collection. The ingest helpers + worker are mocked; this pins the endpoint's
own gating and result aggregation."""
import inspect
import io
import json
import flask
import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, mock_open


def _ctx(files=None):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    data = {}
    if files:
        data["file"] = files
    return app.test_request_context(
        "/api/v1/upload", method="POST", data=data, content_type="multipart/form-data")


def _uploader(role_upload=True, anon=False):
    return SimpleNamespace(is_authenticated=True, is_anonymous=anon,
                           role_upload=lambda: role_upload, id=1, name="maggie")


@pytest.mark.unit
def test_upload_anonymous_401():
    from cps.api import upload as mod
    with _ctx(files=[(io.BytesIO(b"x"), "a.epub")]):
        with patch.object(mod, "current_user", _uploader(anon=True)):
            resp = inspect.unwrap(mod.upload_books)()
    assert resp[1] == 401


@pytest.mark.unit
def test_upload_requires_upload_role():
    from cps.api import upload as mod
    with _ctx(files=[(io.BytesIO(b"x"), "a.epub")]):
        with patch.object(mod, "current_user", _uploader(role_upload=False)):
            resp = inspect.unwrap(mod.upload_books)()
    assert resp[1] == 403


@pytest.mark.unit
def test_upload_no_files_400():
    from cps.api import upload as mod
    with _ctx(files=None):
        with patch.object(mod, "current_user", _uploader()), \
             patch.object(mod, "_ensure_ingest_dir_writable", return_value=None):
            resp = inspect.unwrap(mod.upload_books)()
    assert resp[1] == 400


@pytest.mark.unit
def test_upload_valid_file_queued():
    from cps.api import upload as mod
    with _ctx(files=[(io.BytesIO(b"data"), "book.epub")]):
        with patch.object(mod, "current_user", _uploader()), \
             patch.object(mod, "_ensure_ingest_dir_writable", return_value=None), \
             patch.object(mod, "_validate_uploaded_file", return_value=True), \
             patch.object(mod, "_get_ingest_path", return_value="/ingest/new/book.epub"), \
             patch.object(mod, "_save_to_ingest_atomic_rename",
                          return_value=("/ingest/tmp", "/ingest/new/book.epub")), \
             patch("builtins.open", mock_open()) as manifest_open, \
             patch.object(mod, "os") as mock_os, \
             patch.object(mod, "WorkerThread") as worker, \
             patch.object(mod, "config", SimpleNamespace(config_upload_formats="epub,pdf")):
            resp = inspect.unwrap(mod.upload_books)()
    body = json.loads(resp.get_data())
    assert body["queued"] == ["book.epub"]
    assert body["errors"] == []
    assert mock_os.replace.called and worker.add.called
    manifest_open.assert_called_once_with("/ingest/new/book.epub.cwa.json", "w", encoding="utf-8")


@pytest.mark.unit
def test_upload_invalid_file_reported_not_queued():
    from cps.api import upload as mod
    with _ctx(files=[(io.BytesIO(b"data"), "book.exe")]):
        with patch.object(mod, "current_user", _uploader()), \
             patch.object(mod, "_ensure_ingest_dir_writable", return_value=None), \
             patch.object(mod, "_validate_uploaded_file", return_value=False), \
             patch.object(mod, "WorkerThread") as worker, \
             patch.object(mod, "config", SimpleNamespace(config_upload_formats="epub,pdf")):
            resp = inspect.unwrap(mod.upload_books)()
    body = json.loads(resp.get_data())
    assert body["queued"] == []
    assert body["errors"][0]["filename"] == "book.exe"
    assert not worker.add.called


@pytest.mark.unit
def test_upload_ingest_unwritable_500():
    from cps.api import upload as mod
    with _ctx(files=[(io.BytesIO(b"x"), "a.epub")]):
        with patch.object(mod, "current_user", _uploader()), \
             patch.object(mod, "_ensure_ingest_dir_writable", side_effect=PermissionError("ro")):
            resp = inspect.unwrap(mod.upload_books)()
    assert resp[1] == 500
    assert json.loads(resp[0].get_data())["error"]["code"] == "ingest_unwritable"
