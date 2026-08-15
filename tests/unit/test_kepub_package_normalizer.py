# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for Kobo-safe KEPUB package path normalization."""

import logging
import re
import zipfile

import pytest
from lxml import etree


CONTAINER_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OPS/epb.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

FLATLAND_OPF = b"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">flatland-test</dc:identifier>
  </metadata>
  <manifest>
    <item id="nav" href="../nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter-001.xml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>
"""

CLEAN_OPF = FLATLAND_OPF.replace(b'href="../nav.xhtml"', b'href="nav.xhtml"')

NAV_XHTML = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><nav>
  <a href="OPS/chapter-001.xml#start">Chapter one</a>
  <iframe src="OPS/chapter-001.xml#preview"></iframe>
</nav></body></html>
"""

CHAPTER_XHTML = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
  <a href="../nav.xhtml">Contents</a>
  <p><span class="koboSpan" id="kobo.1.1">A</span>
     <span id="kobo.1.2" class="koboSpan extra">B</span></p>
</body></html>
"""


def _write_package(path, *, opf=FLATLAND_OPF, nav_path="nav.xhtml", extra_entries=()):
    entries = [
        ("mimetype", b"application/epub+zip", zipfile.ZIP_STORED),
        ("META-INF/container.xml", CONTAINER_XML, zipfile.ZIP_DEFLATED),
        ("OPS/epb.opf", opf, zipfile.ZIP_DEFLATED),
        (nav_path, NAV_XHTML, zipfile.ZIP_DEFLATED),
        ("OPS/chapter-001.xml", CHAPTER_XHTML, zipfile.ZIP_DEFLATED),
    ]
    entries.extend((name, content, zipfile.ZIP_DEFLATED) for name, content in extra_entries)
    with zipfile.ZipFile(path, "w") as archive:
        for name, content, compression in entries:
            archive.writestr(name, content, compress_type=compression)


def _normalizer():
    from cps.services.kepub_package_normalizer import normalize_kepub_package

    return normalize_kepub_package


def _manifest_hrefs(opf_bytes):
    root = etree.fromstring(opf_bytes)
    return root.xpath("//*[local-name()='manifest']/*[local-name()='item']/@href")


@pytest.mark.unit
def test_flatland_shape_relocates_escaping_nav_and_preserves_link_targets(tmp_path):
    package = tmp_path / "flatland.kepub"
    _write_package(package)

    assert _normalizer()(package) is True

    with zipfile.ZipFile(package) as archive:
        names = set(archive.namelist())
        opf = archive.read("OPS/epb.opf")
        hrefs = _manifest_hrefs(opf)
        nav_href = hrefs[0]
        nav_path = "OPS/" + nav_href
        nav = archive.read(nav_path)
        chapter = archive.read("OPS/chapter-001.xml")

    assert all(".." not in href.split("/") for href in hrefs)
    assert nav_path in names
    assert "nav.xhtml" not in names
    assert b'href="chapter-001.xml#start"' in nav
    assert b'src="chapter-001.xml#preview"' in nav
    assert b'href="' + nav_href.encode() + b'"' in chapter


@pytest.mark.unit
def test_second_normalization_is_a_byte_identical_no_op(tmp_path):
    package = tmp_path / "flatland.kepub"
    _write_package(package)
    normalize = _normalizer()

    assert normalize(package) is True
    first = package.read_bytes()
    assert normalize(package) is False

    assert package.read_bytes() == first


@pytest.mark.unit
def test_clean_package_is_completely_untouched(tmp_path):
    package = tmp_path / "clean.kepub"
    _write_package(package, opf=CLEAN_OPF, nav_path="OPS/nav.xhtml")
    before = package.read_bytes()

    assert _normalizer()(package) is False

    assert package.read_bytes() == before


@pytest.mark.unit
def test_clean_package_reads_only_container_and_opf(tmp_path, monkeypatch):
    import cps.services.kepub_package_normalizer as normalizer

    package = tmp_path / "clean.kepub"
    _write_package(package, opf=CLEAN_OPF, nav_path="OPS/nav.xhtml")
    read_names = []

    class RecordingZipFile(zipfile.ZipFile):
        def read(self, name, *args, **kwargs):
            read_names.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
            return super().read(name, *args, **kwargs)

    monkeypatch.setattr(normalizer.zipfile, "ZipFile", RecordingZipFile)

    assert normalizer.normalize_kepub_package(package) is False
    assert read_names == ["META-INF/container.xml", "OPS/epb.opf"]


@pytest.mark.unit
def test_kobo_span_markup_and_per_file_counts_are_preserved_exactly(tmp_path):
    package = tmp_path / "spans.kepub"
    _write_package(package)
    span_pattern = re.compile(rb"<span\b[^>]*\bclass=(['\"])\b[^>]*koboSpan[^>]*>.*?</span>", re.DOTALL)

    with zipfile.ZipFile(package) as archive:
        before = {name: len(span_pattern.findall(archive.read(name))) for name in archive.namelist()}
        chapter_before = archive.read("OPS/chapter-001.xml")
        exact_spans = re.findall(rb"<span\b[^>]*>.*?</span>", chapter_before, re.DOTALL)

    assert _normalizer()(package) is True

    with zipfile.ZipFile(package) as archive:
        after = {name: len(span_pattern.findall(archive.read(name))) for name in archive.namelist()}
        chapter_after = archive.read("OPS/chapter-001.xml")

    assert {name: count for name, count in after.items() if count} == {
        name: count for name, count in before.items() if count
    }
    assert all(span in chapter_after for span in exact_spans)


@pytest.mark.unit
def test_mimetype_remains_first_and_stored(tmp_path):
    package = tmp_path / "mimetype.kepub"
    _write_package(package)

    assert _normalizer()(package) is True

    with zipfile.ZipFile(package) as archive:
        first = archive.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert archive.read(first) == b"application/epub+zip"


@pytest.mark.unit
def test_relocation_uses_a_collision_safe_name(tmp_path):
    package = tmp_path / "collision.kepub"
    existing = b"<html xmlns=\"http://www.w3.org/1999/xhtml\"><body>existing</body></html>"
    _write_package(package, extra_entries=(("OPS/nav.xhtml", existing),))

    assert _normalizer()(package) is True

    with zipfile.ZipFile(package) as archive:
        hrefs = _manifest_hrefs(archive.read("OPS/epb.opf"))
        assert hrefs[0] == "nav-1.xhtml"
        assert archive.read("OPS/nav.xhtml") == existing
        assert "OPS/nav-1.xhtml" in archive.namelist()
        assert b'href="nav-1.xhtml"' in archive.read("OPS/chapter-001.xml")


@pytest.mark.unit
def test_corrupt_input_is_preserved_and_only_logs_a_warning(tmp_path, caplog):
    package = tmp_path / "corrupt.kepub"
    original = b"PK\x03\x04not-a-readable-archive"
    package.write_bytes(original)

    with caplog.at_level(logging.WARNING):
        assert _normalizer()(package) is None

    assert package.read_bytes() == original
    assert any("normalize" in record.getMessage().lower() for record in caplog.records)


@pytest.mark.unit
def test_conversion_continues_when_normalization_cannot_process_the_package(tmp_path, monkeypatch):
    """A normalizer that cannot handle a package must not withhold the KEPUB.

    An un-normalized KEPUB is exactly what we shipped before this feature existed.
    Failing the conversion instead drops the user back to EPUB delivery, where a
    Kobo cannot save highlights at all (upstream calibre-web #1484) -- strictly
    worse than the problem normalization is trying to prevent. A genuinely corrupt
    archive is still rejected, but by `_valid_archive`, which is its job.
    """
    from types import SimpleNamespace

    import cps.helper  # noqa: F401 - establish the application's normal import order
    from cps.tasks import convert

    book_path = tmp_path / "book"
    (tmp_path / "book.epub").write_bytes(b"source")
    # a VALID archive that the normalizer simply declines to process
    _write_package(tmp_path / "src.kepub.epub")
    (tmp_path / "book.kepub.epub").write_bytes((tmp_path / "src.kepub.epub").read_bytes())
    destination = tmp_path / "book.kepub"
    process = SimpleNamespace(returncode=0)

    monkeypatch.setattr(convert.config, "config_embed_metadata", False, raising=False)
    monkeypatch.setattr(convert.config, "config_binariesdir", "", raising=False)
    monkeypatch.setattr(convert.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(convert, "process_open", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(convert, "stream_process_output", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(convert, "normalize_kepub_package", lambda _path: None)
    task = convert.TaskConvert(str(book_path), 1, "convert", {}, None)

    check, error = task._convert_kepubify(str(book_path), ".epub", ".kepub")

    assert check == 0, f"conversion should still succeed, got error: {error}"
    assert destination.exists(), "the un-normalized KEPUB must still be delivered"


@pytest.mark.unit
def test_conversion_corrupt_archive_still_does_not_replace_existing_kepub(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import cps.helper  # noqa: F401 - establish the application's normal import order
    from cps.tasks import convert

    book_path = tmp_path / "book"
    (tmp_path / "book.epub").write_bytes(b"source")
    (tmp_path / "book.kepub.epub").write_bytes(b"kepubify output")
    destination = tmp_path / "book.kepub"
    destination.write_bytes(b"existing valid kepub")
    process = SimpleNamespace(returncode=0)

    monkeypatch.setattr(convert.config, "config_embed_metadata", False, raising=False)
    monkeypatch.setattr(convert.config, "config_binariesdir", "", raising=False)
    monkeypatch.setattr(convert.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(convert, "process_open", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(convert, "stream_process_output", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(convert, "normalize_kepub_package", lambda _path: None)
    task = convert.TaskConvert(str(book_path), 1, "convert", {}, None)

    check, error = task._convert_kepubify(str(book_path), ".epub", ".kepub")

    assert check == 1
    assert "invalid kepub archive" in str(error).lower()
    assert destination.read_bytes() == b"existing valid kepub"
