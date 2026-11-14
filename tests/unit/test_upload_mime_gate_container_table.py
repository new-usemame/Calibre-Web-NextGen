# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The upload MIME gate must not depend on the host's /etc/mime.types.

Python's ``mimetypes.types_map`` is built from whatever MIME files the host
ships. The Docker image's table has no entry for .docx, .odt or .flac, all
three of which are default upload formats. ``get_mimetype`` raised KeyError
for them, ``validate_mime_type`` logged "Unkown mimetype for Extension" at
ERROR on every upload (#1026's log), and, with extension checking on (the
default), a real .docx was refused because its sniffed type was in no allowed
list. On a developer Mac the host table has all three, which is why it never
showed outside the container.

The container table is modelled here by removing those entries from a copy
of ``types_map``; libmagic is replaced by a stub returning the type the image's
libmagic reports for each format (measured in the :dev image, 2026-11-13).
"""
import io
import mimetypes
from types import SimpleNamespace

import pytest
from werkzeug.datastructures import FileStorage

pytestmark = pytest.mark.unit

import cps  # noqa: F401,E402  (registers application MIME types)
from cps import constants, file_helper  # noqa: E402

# What the image's libmagic reports for a real file of each format.
SNIFFED = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "odt": "application/vnd.oasis.opendocument.text",
    "flac": "audio/flac",
    "fbz": "application/zip",
}
# Extensions the image's mimetypes table does not know.
ABSENT_IN_IMAGE = (".docx", ".odt", ".flac", ".fbz")


@pytest.fixture
def container_table(monkeypatch):
    table = {k: v for k, v in mimetypes.types_map.items() if k not in ABSENT_IN_IMAGE}
    monkeypatch.setattr(mimetypes, "types_map", table)
    monkeypatch.setattr(file_helper, "error", None)
    errors = []
    monkeypatch.setattr(file_helper, "log", SimpleNamespace(
        error=lambda msg, *a, **k: errors.append(msg % a if a else msg),
        debug=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        info=lambda *a, **k: None,
    ))
    return errors


def _magic_returning(sniffed):
    class _Magic:
        def __init__(self, mime=True):
            pass

        def from_buffer(self, body):
            return sniffed
    return SimpleNamespace(Magic=_Magic)


@pytest.mark.parametrize("ext", sorted(SNIFFED))
def test_default_upload_format_passes_the_gate_on_the_image_table(container_table, monkeypatch, ext):
    monkeypatch.setattr(file_helper, "magic", _magic_returning(SNIFFED[ext]), raising=False)
    upload = FileStorage(stream=io.BytesIO(b"body"), filename="book." + ext)

    assert file_helper.validate_mime_type(upload, sorted(constants.EXTENSIONS_UPLOAD)) is True
    assert container_table == []


def test_every_default_upload_format_resolves_on_the_image_table(container_table):
    unresolved = []
    for ext in sorted(constants.EXTENSIONS_UPLOAD):
        try:
            file_helper.get_mimetype("." + ext)
        except KeyError:
            unresolved.append(ext)
    assert unresolved == []


def test_an_unmapped_allowed_extension_is_not_an_error(container_table, monkeypatch):
    """An admin may allow an extension nobody has a MIME type for. The gate
    still decides by the sniffed type; the gap is not an error to log on
    every upload."""
    monkeypatch.setattr(file_helper, "magic", _magic_returning("application/epub+zip"), raising=False)
    upload = FileStorage(stream=io.BytesIO(b"body"), filename="book.epub")

    assert file_helper.validate_mime_type(upload, ["epub", "zzunknown"]) is True
    assert container_table == []


def test_a_type_outside_the_allowlist_is_still_refused(container_table, monkeypatch):
    monkeypatch.setattr(file_helper, "magic", _magic_returning("application/x-dosexec"), raising=False)
    upload = FileStorage(stream=io.BytesIO(b"MZ"), filename="book.docx")

    assert file_helper.validate_mime_type(upload, ["docx", "epub"]) is False
