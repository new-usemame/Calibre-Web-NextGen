# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Calibre exports FB2 books as ``Title.fb2.zip`` while naming the format
"fbz" everywhere else — its input plugins and this project's ingest pipeline
both match ".fbz" only. The upload gates read just the last dotted segment,
so the file was rejected as extension "zip" even with FB2 uploads enabled,
and a hand-renamed ".fbz" logged an unknown-mimetype error because no MIME
type was registered for it.

These tests pin the boundary fix: the double extension is normalized to
".fbz" at the upload gate and in the ingest staging name, ".fbz" carries a
real MIME type and ships in the default upload formats, and existing FB2
allowlists inherit it once via the same one-time reconcile LCPL got.
"""
import io
import os
from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest
from werkzeug.datastructures import FileStorage

pytestmark = pytest.mark.unit

import cps  # noqa: F401,E402  (registers application MIME types)
from cps import constants, editbooks, file_helper  # noqa: E402


class _ZipMagic:
    """Deterministic stand-in for libmagic on a Zip-container body."""

    def __init__(self, mime=True):
        assert mime is True

    def from_buffer(self, body):
        return "application/zip"


def _config(formats, *, migrated=False):
    from cps.config_sql import ConfigSQL

    config = ConfigSQL()
    config.config_upload_formats = formats
    config.config_upload_formats_fbz_migrated = migrated
    saves = []

    def _save():
        saves.append(
            (
                config.config_upload_formats,
                config.config_upload_formats_fbz_migrated,
            )
        )

    config.save = _save
    return config, saves


def test_normalized_upload_filename_maps_fb2_zip_to_fbz():
    assert editbooks._normalized_upload_filename("Metro 2033.fb2.zip") == "Metro 2033.fbz"
    assert editbooks._normalized_upload_filename("Metro 2033.FB2.ZIP") == "Metro 2033.fbz"
    assert editbooks._normalized_upload_filename("book.epub") == "book.epub"
    assert editbooks._normalized_upload_filename("archive.zip") == "archive.zip"
    assert editbooks._normalized_upload_filename("fb2.zip") == "fb2.zip"
    assert editbooks._normalized_upload_filename(None) == ""


def test_uploaded_file_extension_after_normalization():
    assert editbooks._uploaded_file_extension("Metro 2033.fb2.zip") == "fbz"
    assert editbooks._uploaded_file_extension("Metro 2033.FB2.ZIP") == "fbz"
    assert editbooks._uploaded_file_extension("book.epub") == "epub"
    assert editbooks._uploaded_file_extension("no_extension") is None
    assert editbooks._uploaded_file_extension(None) is None


def test_fbz_has_a_registered_mimetype():
    assert file_helper.get_mimetype('.fbz') == 'application/zip'


def test_fbz_is_a_default_upload_format():
    assert 'fbz' in constants.EXTENSIONS_UPLOAD


def test_upload_gate_accepts_fb2_zip_when_fbz_allowed(monkeypatch):
    """The real end-to-end gate: a zip-sniffing body named "X.fb2.zip" clears
    validation with just fbz in the allowed formats."""
    config = SimpleNamespace(config_check_extensions=True,
                             config_upload_formats="fbz")
    upload = FileStorage(
        stream=io.BytesIO(b"PK\x03\x04"),
        filename="Metro 2033.fb2.zip",
        content_type="application/zip",
    )
    monkeypatch.setattr(file_helper, "error", None)
    monkeypatch.setattr(
        file_helper,
        "magic",
        SimpleNamespace(Magic=_ZipMagic),
        raising=False,
    )

    app = flask.Flask(__name__)
    app.secret_key = "test"
    with app.test_request_context("/upload", method="POST"), \
            patch.object(editbooks, "config", config), \
            patch.object(editbooks, "_", lambda text, **_kwargs: text):
        assert editbooks._validate_uploaded_file(upload) is True


def test_upload_gate_rejects_fb2_zip_when_fbz_not_allowed(monkeypatch):
    """Normalization must not widen the allowlist: with fbz absent the gate
    refuses, and the extension shown is the canonical one."""
    config = SimpleNamespace(config_check_extensions=False,
                             config_upload_formats="epub")
    upload = FileStorage(
        stream=io.BytesIO(b"PK\x03\x04"),
        filename="Metro 2033.fb2.zip",
        content_type="application/zip",
    )

    app = flask.Flask(__name__)
    app.secret_key = "test"
    with app.test_request_context("/upload", method="POST"), \
            patch.object(editbooks, "config", config), \
            patch.object(editbooks, "_", lambda text, **_kwargs: text):
        assert editbooks._validate_uploaded_file(upload) is False


def test_get_ingest_path_stages_fb2_zip_as_fbz(tmp_path, monkeypatch):
    """The staged file the ingest service sees carries ".fbz" — the only form
    its format matching knows (Path(...).suffix of "X.fb2.zip" would be
    "zip", which is not a supported book format)."""
    monkeypatch.setattr(editbooks, "get_ingest_dir", lambda: str(tmp_path))

    uploaded = FileStorage(stream=io.BytesIO(b"PK\x03\x04"),
                           filename="Metro 2033.fb2.zip")
    final_path = editbooks._get_ingest_path(uploaded, prefix_parts=["new", 1])

    component = os.path.basename(final_path)
    assert component.endswith(".fbz")
    assert ".fb2.zip" not in component


def test_existing_allowlist_appends_fbz_once_when_fb2_present():
    config, saves = _config("mobi,epub,pdf,fb2")

    config.reconcile_fbz_upload_format()

    assert config.config_upload_formats == "mobi,epub,pdf,fb2,fbz"
    assert config.config_upload_formats_fbz_migrated is True
    assert saves == [("mobi,epub,pdf,fb2,fbz", True)]


def test_allowlist_without_fb2_is_untouched_but_marked():
    config, saves = _config("epub,pdf")

    config.reconcile_fbz_upload_format()

    assert config.config_upload_formats == "epub,pdf"
    assert config.config_upload_formats_fbz_migrated is True
    assert saves == [("epub,pdf", True)]


def test_allowlist_already_containing_fbz_is_untouched_but_marked():
    original = "epub,fbz,pdf"
    config, saves = _config(original)

    config.reconcile_fbz_upload_format()

    assert config.config_upload_formats == original
    assert config.config_upload_formats_fbz_migrated is True
    assert saves == [(original, True)]


def test_running_migration_twice_does_not_append_twice():
    config, saves = _config("epub,fb2")
    config.reconcile_fbz_upload_format()

    config.reconcile_fbz_upload_format()

    assert config.config_upload_formats == "epub,fb2,fbz"
    assert len(saves) == 1
