# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fork #1608: Readium LCP licence files need ACSM-shaped handling.

An .lcpl is a fulfilment ticket rather than an ebook. It must pass the default
upload gates so an LCP-capable Calibre plugin can fulfil it, while a failed
conversion must leave the ticket in processed_books/failed instead of filing a
junk library entry.
"""

import ast
import io
import mimetypes
import re
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest
from werkzeug.datastructures import FileStorage

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(REPO_ROOT / "scripts")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import cps  # noqa: E402,F401  (registers application MIME types)
from cps import constants, editbooks, file_helper  # noqa: E402
import ingest_processor  # noqa: E402


LCP_LICENSE = b'''{
  "id": "https://example.invalid/licenses/loan-123",
  "issued": "2026-08-16T12:00:00Z",
  "updated": "2026-08-16T12:00:00Z",
  "encryption": {
    "profile": "http://readium.org/lcp/basic-profile",
    "content_key": {"encrypted_value": "AA=="},
    "user_key": {
      "text_hint": "Enter your passphrase",
      "algorithm": "http://www.w3.org/2001/04/xmlenc#sha256"
    }
  },
  "links": [{
    "rel": "publication",
    "href": "https://example.invalid/books/loan-123.epub",
    "type": "application/epub+zip"
  }],
  "user": {"id": "reader-123"},
  "rights": {"print": 0, "copy": 0},
  "signature": {
    "certificate": "AA==",
    "value": "AA==",
    "algorithm": "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
  }
}'''

INGEST_SERVICE_RUN = (
    REPO_ROOT / "root/etc/s6-overlay/s6-rc.d/cwa-ingest-service/run"
)


class _JsonMagic:
    """Deterministic stand-in for the unavailable native libmagic library."""

    def __init__(self, mime=True):
        assert mime is True

    def from_buffer(self, body):
        assert b'"encryption"' in body and b'"publication"' in body
        return "application/json"


class _XmlMagic:
    """The real container's libmagic result for an ACSM XML body."""

    def __init__(self, mime=True):
        assert mime is True

    def from_buffer(self, body):
        assert b"<fulfillmentToken" in body
        return "text/xml"


def test_default_upload_gate_accepts_a_realistic_lcpl(monkeypatch):
    """The reporter's file must clear both real default-config upload gates."""
    default_config = SimpleNamespace(
        config_check_extensions=True,
        config_upload_formats=','.join(constants.EXTENSIONS_UPLOAD),
    )
    upload = FileStorage(
        stream=io.BytesIO(LCP_LICENSE),
        filename="Library Loan.lcpl",
        content_type="application/vnd.readium.lcp.license.v1.0+json",
    )

    monkeypatch.setattr(file_helper, "error", None)
    monkeypatch.setattr(
        file_helper,
        "magic",
        SimpleNamespace(Magic=_JsonMagic),
        raising=False,
    )
    allowed = default_config.config_upload_formats.split(',')
    mime_allowed = file_helper.validate_mime_type(upload, allowed)

    app = flask.Flask(__name__)
    app.secret_key = "test"
    with app.test_request_context("/upload", method="POST"), \
            patch.object(editbooks, "config", default_config), \
            patch.object(editbooks, "_", lambda text, **_kwargs: text):
        gate_allowed = editbooks._validate_uploaded_file(upload)

    assert mime_allowed is True
    assert gate_allowed is True


def test_lcpl_is_a_default_upload_format_with_a_registered_mimetype():
    assert 'lcpl' in constants.EXTENSIONS_UPLOAD
    assert mimetypes.types_map['.lcpl'] == (
        'application/vnd.readium.lcp.license.v1.0+json'
    )
    assert file_helper.get_mimetype('.lcpl') == 'application/json'


def test_acsm_mime_validation_does_not_depend_on_fb2_being_allowed(monkeypatch):
    """ACSM must match its sniffed XML type without an incidental FB2 alias."""
    upload = FileStorage(
        stream=io.BytesIO(
            b'<?xml version="1.0"?><fulfillmentToken>loan</fulfillmentToken>'
        ),
        filename="Ticket.acsm",
        content_type="application/vnd.adobe.adept+xml",
    )
    monkeypatch.setattr(file_helper, "error", None)
    monkeypatch.setattr(
        file_helper,
        "magic",
        SimpleNamespace(Magic=_XmlMagic),
        raising=False,
    )

    assert file_helper.validate_mime_type(upload, ['acsm']) is True


def _processor_with_default_formats(monkeypatch, tmp_path):
    class _FakeDb:
        cwa_settings = {
            'auto_convert': True,
            'auto_convert_target_format': 'epub',
            'auto_ingest_ignored_formats': [],
            'auto_convert_ignored_formats': [],
            'auto_convert_retained_formats': [],
            'kindle_epub_fixer': False,
        }

    app_db = tmp_path / "app.db"
    with sqlite3.connect(app_db) as connection:
        connection.execute(
            "CREATE TABLE settings (config_calibre_dir TEXT)"
        )
        connection.execute("INSERT INTO settings VALUES ('')")

    ingest = tmp_path / "ingest"
    library = tmp_path / "library"
    conversion = tmp_path / "conversion"
    ingest.mkdir()
    library.mkdir()

    monkeypatch.setattr(ingest_processor, "CWA_DB", _FakeDb)
    monkeypatch.setattr(
        ingest_processor, "get_app_db_path", lambda: str(app_db)
    )
    monkeypatch.setattr(
        ingest_processor.NewBookProcessor,
        "get_dirs",
        lambda self, _path: (str(ingest), str(library), str(conversion)),
    )
    monkeypatch.setattr(
        ingest_processor.NewBookProcessor,
        "get_split_library",
        lambda self: None,
    )
    monkeypatch.setattr(
        ingest_processor.NewBookProcessor,
        "_get_title_sort_regex",
        staticmethod(lambda: ""),
    )
    return ingest_processor.NewBookProcessor(str(tmp_path / "loan.lcpl"))


def test_lcpl_is_ingestable_but_never_a_conversion_target(monkeypatch, tmp_path):
    assert ingest_processor.is_rescuable_on_conversion_failure('lcpl') is False
    processor = _processor_with_default_formats(monkeypatch, tmp_path)
    assert processor.can_convert is True
    assert processor.input_format == 'lcpl'

    convert_library_tree = ast.parse(
        (REPO_ROOT / "scripts/convert_library.py").read_text()
    )
    live_target_formats = next(
        ast.literal_eval(node.value)
        for node in ast.walk(convert_library_tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute)
            and target.attr == "hierarchy_of_success"
            for target in node.targets
        )
    )
    assert 'lcpl' not in live_target_formats


def test_ingest_watcher_and_processor_support_the_same_extensions(
    monkeypatch, tmp_path
):
    """The shell dispatch gate must not drift from processor capabilities."""
    match = re.search(
        r"^SUPPORTED_EXT_REGEX='\(([^)]*)\)\$'",
        INGEST_SERVICE_RUN.read_text(),
        re.MULTILINE,
    )
    assert match is not None
    watcher_formats = set(match.group(1).split('|'))

    processor = _processor_with_default_formats(monkeypatch, tmp_path)
    processor_formats = (
        processor.supported_book_formats
        | processor.supported_audiobook_formats
    )
    assert watcher_formats == processor_formats


def test_lcpl_failure_guidance_is_specific_without_changing_acsm():
    lcpl = ingest_processor.conversion_failure_guidance(
        'lcpl', 'Library Loan.lcpl'
    )
    assert lcpl is not None
    assert lcpl.startswith("LCPL_NOTICE:")
    assert "Library Loan.lcpl" in lcpl
    assert "CWA_CALIBRE_USER_PLUGINS" in lcpl
    assert "/config/.config/calibre/plugins" in lcpl
    assert "EPUB/PDF" in lcpl
    assert "processed_books/failed" in lcpl
    assert "requires an LCP-capable Calibre plugin" in lcpl
    assert "if calibre attempted fulfilment and one is installed" in lcpl.lower()
    assert "its own output appears above this line" in lcpl

    acsm = ingest_processor.conversion_failure_guidance('acsm', 'Ticket.acsm')
    assert acsm is not None
    assert acsm.startswith("ACSM_NOTICE:")
    assert "Ticket.acsm" in acsm
    assert "CWA_CALIBRE_USER_PLUGINS" in acsm
    assert "Adobe Digital Editions" in acsm
    assert "LCPL_NOTICE:" not in acsm
