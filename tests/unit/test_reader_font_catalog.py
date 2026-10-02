# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioral checks for the shared uploaded EPUB-font catalog."""
from io import BytesIO

import pytest


@pytest.fixture
def font_catalog(tmp_path, monkeypatch):
    from cps import constants
    from cps.services import reader_fonts

    monkeypatch.setattr(constants, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(reader_fonts, "validate_font_bytes", lambda payload, extension: {
        "family": "Fixture Family", "style": "Regular", "bytes_uncompressed": 1024,
    })
    return reader_fonts


def _font_stream(data=b"fixture-font", signature=b"\x00\x01\x00\x00"):
    return BytesIO(signature + data)


def test_uploaded_catalogue_retains_bundled_literata_choice(font_catalog):
    """Uploading a face must preserve the already shipped bundled EPUB choice."""
    for has_upload in (False, True):
        if has_upload:
            font_catalog.upload_font(_font_stream(), "book-face.ttf")
        options = font_catalog.catalogue()["items"]
        literata = next((item for item in options if item["id"] == "Literata"), None)
        assert literata is not None, "the catalog must retain the bundled Literata choice"
        assert literata["family"] == "'Literata', serif"
        assert literata["builtin"] is True


def test_upload_catalogue_deduplicates_bytes_and_delete_invalidates_choice(font_catalog):
    catalog = font_catalog
    first, created = catalog.upload_font(_font_stream(), "book-face.ttf", "My Book Font")
    duplicate, duplicate_created = catalog.upload_font(
        _font_stream(), "other-name.ttf", "Different label",
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate["id"] == first["id"]
    listed = catalog.catalogue(lambda font_id: "/api/v1/reader/fonts/%s/file" % font_id)
    option = next(item for item in listed["items"] if item["id"] == "custom:" + first["id"])
    assert option["label"] == "My Book Font"
    assert option["family"] == "CWNGUpload_" + first["id"].replace("-", "")
    assert option["url"].endswith("/file")
    assert listed["limits"]["used_fonts"] == 1

    assert catalog.delete_font(first["id"]) is True
    assert catalog.delete_font(first["id"]) is False
    assert "custom:" + first["id"] not in catalog.custom_font_ids()
    assert all(item["id"] != "custom:" + first["id"] for item in catalog.catalogue()["items"])


def test_open_font_remains_readable_if_admin_deletes_during_response(font_catalog):
    catalog = font_catalog
    record, _ = catalog.upload_font(_font_stream(), "font.ttf")
    opened = catalog.open_file_for_font(record["id"])
    assert opened is not None
    stored_record, handle = opened
    assert catalog.delete_font(record["id"])
    try:
        assert handle.read() == b"\x00\x01\x00\x00fixture-font"
        assert stored_record["id"] == record["id"]
    finally:
        handle.close()


def test_catalog_ignores_invalid_metadata_families(font_catalog, tmp_path):
    catalog = font_catalog
    record, _ = catalog.upload_font(_font_stream(), "font.ttf")
    metadata = catalog.root_dir() / record["id"] / "font.json"
    contents = metadata.read_text(encoding="utf-8").replace(
        "CWNGUpload_" + record["id"].replace("-", ""), 'evil;}</style><script>'
    )
    metadata.write_text(contents, encoding="utf-8")

    assert all(item["id"] != "custom:" + record["id"] for item in catalog.catalogue()["items"])


def test_upload_accepts_legacy_true_type_signature(font_catalog):
    record, created = font_catalog.upload_font(
        _font_stream(signature=b"true"), "legacy-face.ttf",
    )
    assert created is True
    assert record["extension"] == ".ttf"


def test_upload_rejects_mismatched_signature_before_parsing(font_catalog, monkeypatch):
    from cps.services import reader_fonts

    def unexpected_parse(*_args):
        pytest.fail("a mismatched file signature must be rejected before parsing")

    monkeypatch.setattr(reader_fonts, "validate_font_bytes", unexpected_parse)
    with pytest.raises(reader_fonts.ReaderFontError, match="contents do not match"):
        reader_fonts.upload_font(BytesIO(b"not-font"), "spoofed.ttf")


def test_full_catalog_rejects_before_running_font_parser(font_catalog, monkeypatch):
    from cps.services import reader_fonts

    monkeypatch.setattr(reader_fonts, "MAX_FONT_COUNT", 1)
    first, _ = reader_fonts.upload_font(_font_stream(b"first"), "first.ttf")
    assert first
    monkeypatch.setattr(reader_fonts, "validate_font_bytes", lambda *_args: pytest.fail(
        "a full catalog should reject before parsing another font"
    ))
    with pytest.raises(reader_fonts.ReaderFontError, match="limit has been reached"):
        reader_fonts.upload_font(_font_stream(b"second"), "second.ttf")


def test_validator_exec_scope_matches_calibre_command_runner(monkeypatch, capsys):
    """Calibre executes -c with distinct locals; valid tables must still emit success."""
    import sys
    import types
    import resource
    from cps.services import reader_fonts

    class Font:
        def keys(self):
            return ['GlyphOrder', 'head', 'maxp', 'cmap', 'name']

        def ensureDecompiled(self, recurse):
            assert recurse is True

        def __getitem__(self, tag):
            if tag == 'maxp':
                return types.SimpleNamespace(numGlyphs=2)
            if tag == 'name':
                return types.SimpleNamespace(getDebugName=lambda value: 'Family' if value in (1, 16) else 'Regular')
            raise KeyError(tag)

        def getBestCmap(self):
            return {65: 'A'}

        def getTableData(self, tag):
            return b'usable table'

    module = types.ModuleType('fontTools.ttLib')
    module.TTFont = lambda *_args, **_kwargs: Font()
    monkeypatch.setitem(sys.modules, 'fontTools.ttLib', module)
    monkeypatch.setattr(resource, 'setrlimit', lambda *_args: None)
    monkeypatch.setattr(sys, 'argv', ['calibre-debug', 'fixture.ttf'])
    exec(reader_fonts._VALIDATOR_CODE, {}, {})
    assert '"ok": true' in capsys.readouterr().out
