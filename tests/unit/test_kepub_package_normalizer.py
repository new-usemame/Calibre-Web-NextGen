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

SPLIT_OPF = b"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="chapter"/></spine>
</package>
"""

SPLIT_NCX = b"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>
  <navPoint id="one"><navLabel><text>One</text></navLabel>
    <content src="chapter.xhtml#one"/></navPoint>
  <navPoint id="two"><navLabel><text>Two</text></navLabel>
    <content src="chapter.xhtml#two"/></navPoint>
</navMap></ncx>
"""

SPLIT_CHAPTER = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
  <div id="book-columns"><div id="book-inner">
    <section id="one"><span class="koboSpan" id="kobo.1.1">one</span></section>
    <section id="two"><span class="koboSpan" id="kobo.2.1">two</span></section>
  </div></div>
</body></html>
"""

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


def _write_package(
        path, *, opf=FLATLAND_OPF, nav_path="nav.xhtml",
        nav_content=NAV_XHTML, chapter_content=CHAPTER_XHTML, extra_entries=()):
    entries = [
        ("mimetype", b"application/epub+zip", zipfile.ZIP_STORED),
        ("META-INF/container.xml", CONTAINER_XML, zipfile.ZIP_DEFLATED),
        ("OPS/epb.opf", opf, zipfile.ZIP_DEFLATED),
        (nav_path, nav_content, zipfile.ZIP_DEFLATED),
        ("OPS/chapter-001.xml", chapter_content, zipfile.ZIP_DEFLATED),
    ]
    entries.extend((name, content, zipfile.ZIP_DEFLATED) for name, content in extra_entries)
    with zipfile.ZipFile(path, "w") as archive:
        for name, content, compression in entries:
            archive.writestr(name, content, compress_type=compression)


def _write_splittable_package(path):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", CONTAINER_XML)
        archive.writestr("OPS/epb.opf", SPLIT_OPF)
        archive.writestr("OPS/toc.ncx", SPLIT_NCX)
        archive.writestr("OPS/chapter.xhtml", SPLIT_CHAPTER)


def _ncx_sources(path):
    with zipfile.ZipFile(path) as archive:
        document = etree.fromstring(archive.read("OPS/toc.ncx"))
    return document.xpath("//*[local-name()='navMap']//*[local-name()='content']/@src")


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
def test_escaping_href_relocation_composes_with_redundant_toc_fragment_strip(tmp_path):
    package = tmp_path / "composed.kepub"
    nav = NAV_XHTML.replace(
        b"<html xmlns=\"http://www.w3.org/1999/xhtml\"><body><nav>",
        b"<html xmlns=\"http://www.w3.org/1999/xhtml\" "
        b"xmlns:epub=\"http://www.idpf.org/2007/ops\"><body><nav epub:type=\"toc\">",
    )
    chapter = CHAPTER_XHTML.replace(
        b'<a href="../nav.xhtml">Contents</a>',
        b'<a id="start" href="../nav.xhtml">Contents</a>',
    )
    _write_package(package, nav_content=nav, chapter_content=chapter)

    assert _normalizer()(package) is True

    with zipfile.ZipFile(package) as archive:
        hrefs = _manifest_hrefs(archive.read("OPS/epb.opf"))
        relocated_nav = "OPS/" + hrefs[0]
        rewritten_nav = archive.read(relocated_nav)
        assert relocated_nav in archive.namelist()
    assert b'href="chapter-001.xml"' in rewritten_nav
    assert b'href="chapter-001.xml#start"' not in rewritten_nav


@pytest.mark.unit
def test_repair_probe_reports_redundant_toc_fragment(tmp_path):
    from cps.services import kepub_package_normalizer as normalizer

    opf = CLEAN_OPF
    nav = NAV_XHTML.replace(
        b"<html xmlns=\"http://www.w3.org/1999/xhtml\"><body><nav>",
        b"<html xmlns=\"http://www.w3.org/1999/xhtml\" "
        b"xmlns:epub=\"http://www.idpf.org/2007/ops\"><body><nav epub:type=\"toc\">",
    ).replace(b'href="OPS/chapter-001.xml#start"', b'href="chapter-001.xml#start"')
    chapter = CHAPTER_XHTML.replace(
        b'<a href="../nav.xhtml">Contents</a>',
        b'<a id="start" href="../nav.xhtml">Contents</a>',
    )
    package = tmp_path / "probe-fragment.kepub"
    _write_package(
        package, opf=opf, nav_path="OPS/nav.xhtml",
        nav_content=nav, chapter_content=chapter)

    inspection = normalizer.kepub_package_needs_normalization(package)

    assert inspection.status == normalizer.PROBE_NEEDS_NORMALIZATION


@pytest.mark.unit
def test_clean_package_is_completely_untouched(tmp_path):
    package = tmp_path / "clean.kepub"
    _write_package(package, opf=CLEAN_OPF, nav_path="OPS/nav.xhtml")
    before = package.read_bytes()

    assert _normalizer()(package) is False

    assert package.read_bytes() == before


@pytest.mark.unit
def test_clean_package_reads_only_bounded_structural_documents(tmp_path, monkeypatch):
    import cps.services.kepub_package_normalizer as normalizer

    package = tmp_path / "clean.kepub"
    _write_package(package, opf=CLEAN_OPF, nav_path="OPS/nav.xhtml")
    read_names = []

    class RecordingZipFile(zipfile.ZipFile):
        def open(self, name, *args, **kwargs):
            read_names.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
            return super().open(name, *args, **kwargs)

    monkeypatch.setattr(normalizer.zipfile, "ZipFile", RecordingZipFile)

    assert normalizer.normalize_kepub_package(package) is False
    assert read_names == ["META-INF/container.xml", "OPS/epb.opf", "OPS/nav.xhtml"]


@pytest.mark.unit
def test_clean_repair_probe_reads_only_bounded_structural_documents(tmp_path, monkeypatch):
    import cps.services.kepub_package_normalizer as normalizer

    package = tmp_path / "clean-probe.kepub"
    _write_package(package, opf=CLEAN_OPF, nav_path="OPS/nav.xhtml")
    read_names = []

    class RecordingZipFile(zipfile.ZipFile):
        def open(self, name, *args, **kwargs):
            read_names.append(name.filename if isinstance(name, zipfile.ZipInfo) else name)
            return super().open(name, *args, **kwargs)

    monkeypatch.setattr(normalizer.zipfile, "ZipFile", RecordingZipFile)

    inspection = normalizer.kepub_package_needs_normalization(package)
    assert inspection.status == normalizer.PROBE_CLEAN
    assert read_names == ["META-INF/container.xml", "OPS/epb.opf", "OPS/nav.xhtml"]


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
    monkeypatch.setattr(
        convert, "normalize_kepub_package", lambda _path, **_kwargs: None)
    task = convert.TaskConvert(str(book_path), 1, "convert", {}, None)

    check, error = task._convert_kepubify(str(book_path), ".epub", ".kepub")

    assert check == 0, f"conversion should still succeed, got error: {error}"
    assert destination.exists(), "the un-normalized KEPUB must still be delivered"


def _patch_annotation_store(monkeypatch, convert, *, annotations):
    """Give the conversion task a readable annotation store.

    `_book_has_annotations` fails CLOSED, so without this every conversion test
    would silently take the no-split path and the split assertions would pass for
    the wrong reason.
    """
    class _Query:
        def filter(self, *_criteria):
            return self

        def first(self):
            return object() if annotations else None

    class _Session:
        def query(self, _model):
            return _Query()

        def close(self):
            pass

    monkeypatch.setattr(convert.ub, "init_db_thread", lambda: _Session(), raising=False)


@pytest.mark.unit
def test_kepubify_conversion_is_born_with_split_chapter_documents(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import cps.helper  # noqa: F401 - establish the application's normal import order
    from cps.tasks import convert

    book_path = tmp_path / "book"
    (tmp_path / "book.epub").write_bytes(b"source")
    _write_splittable_package(tmp_path / "book.kepub.epub")
    destination = tmp_path / "book.kepub"
    process = SimpleNamespace(returncode=0)

    monkeypatch.setattr(convert.config, "config_embed_metadata", False, raising=False)
    monkeypatch.setattr(convert.config, "config_binariesdir", "", raising=False)
    monkeypatch.setattr(convert.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(convert, "process_open", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(convert, "stream_process_output", lambda *_args, **_kwargs: [])
    _patch_annotation_store(monkeypatch, convert, annotations=0)
    task = convert.TaskConvert(str(book_path), 1, "convert", {}, None)

    check, error = task._convert_kepubify(str(book_path), ".epub", ".kepub")

    assert check == 0, error
    assert _ncx_sources(destination) == [
        "chapter-split-1.xhtml",
        "chapter-split-2.xhtml",
    ]


@pytest.mark.unit
def test_kepubify_conversion_does_not_split_a_book_that_has_annotations(
        tmp_path, monkeypatch):
    """Conversion regenerates the KEPUB of a book already in the library.

    A book that has been read and highlighted can be re-converted at any time.
    Splitting renames its spine documents, and a Kobo matches its stored Bookmark
    rows by ContentID -- it would keep the rows, rewrite each to the bare old
    filename, render nothing, and report no annotations. Normalization still runs
    (it never renames anything); only the split is withheld.
    """
    from types import SimpleNamespace

    import cps.helper  # noqa: F401 - establish the application's normal import order
    from cps.tasks import convert

    book_path = tmp_path / "book"
    (tmp_path / "book.epub").write_bytes(b"source")
    _write_splittable_package(tmp_path / "book.kepub.epub")
    destination = tmp_path / "book.kepub"
    process = SimpleNamespace(returncode=0)

    monkeypatch.setattr(convert.config, "config_embed_metadata", False, raising=False)
    monkeypatch.setattr(convert.config, "config_binariesdir", "", raising=False)
    monkeypatch.setattr(convert.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(convert, "process_open", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(convert, "stream_process_output", lambda *_args, **_kwargs: [])
    _patch_annotation_store(monkeypatch, convert, annotations=1)
    task = convert.TaskConvert(str(book_path), 1, "convert", {}, None)

    check, error = task._convert_kepubify(str(book_path), ".epub", ".kepub")

    assert check == 0, error
    assert _ncx_sources(destination) == ["chapter.xhtml#one", "chapter.xhtml#two"], (
        "an annotated book's KEPUB was split; its existing highlights would "
        "stop rendering on the device")


@pytest.mark.unit
def test_the_conversion_annotation_check_fails_closed(monkeypatch):
    """An unreadable annotation store must produce the pre-split behaviour."""
    import cps.helper  # noqa: F401
    from cps.tasks import convert

    def _explode():
        raise RuntimeError("annotation store unavailable")

    monkeypatch.setattr(convert.ub, "init_db_thread", _explode, raising=False)
    task = convert.TaskConvert("/nowhere", 1, "convert", {}, None)
    assert task._book_has_annotations() is True


@pytest.mark.unit
def test_the_conversion_annotation_check_uses_a_worker_session(monkeypatch):
    """Background tasks must not touch the global web-request ub.session.

    Executed rather than pinned to source: fail the global session outright and
    require the check to still work, which it can only do through
    ub.init_db_thread().
    """
    import cps.helper  # noqa: F401
    from cps.tasks import convert

    class _Forbidden:
        def query(self, *_args, **_kwargs):
            raise AssertionError(
                "the conversion task used the global web-request ub.session")

    opened = []

    class _WorkerQuery:
        def filter(self, *_criteria):
            return self

        def first(self):
            return None

    class _WorkerSession:
        def query(self, _model):
            return _WorkerQuery()

        def close(self):
            opened.append("closed")

    monkeypatch.setattr(convert.ub, "session", _Forbidden(), raising=False)
    monkeypatch.setattr(convert.ub, "init_db_thread", lambda: _WorkerSession(), raising=False)

    task = convert.TaskConvert("/nowhere", 1, "convert", {}, None)
    assert task._book_has_annotations() is False
    assert opened == ["closed"], "the worker session was not closed"


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
    monkeypatch.setattr(
        convert, "normalize_kepub_package", lambda _path, **_kwargs: None)
    task = convert.TaskConvert(str(book_path), 1, "convert", {}, None)

    check, error = task._convert_kepubify(str(book_path), ".epub", ".kepub")

    assert check == 1
    assert "invalid kepub archive" in str(error).lower()
    assert destination.read_bytes() == b"existing valid kepub"


# --- hardening: gaps found by adversarial review of #1637 ---

ABSOLUTE_HREF_OPF = FLATLAND_OPF.replace(b'href="../nav.xhtml"', b'href="/nav.xhtml"')


@pytest.mark.unit
def test_an_absolute_manifest_href_is_not_silently_treated_as_external(tmp_path, caplog):
    """`None` from the reference splitter means "not ours to touch", and every
    caller treats it as skip. An absolute local path is not external -- it is a
    reference we cannot contain -- so lumping it in with http:/data: would let a
    manifest href escape the OPF directory while we report the package clean.
    That is precisely the invariant this module exists to guarantee.
    """
    package = tmp_path / "absolute.kepub"
    _write_package(package, opf=ABSOLUTE_HREF_OPF)
    original = package.read_bytes()

    with caplog.at_level("WARNING"):
        result = _normalizer()(package)

    assert result is None, "an uncontainable href must not report success"
    assert package.read_bytes() == original, "the original must be left untouched"
    assert any("normalize" in r.getMessage().lower() for r in caplog.records)


@pytest.mark.unit
def test_a_genuinely_external_href_is_still_left_alone(tmp_path):
    """The fix must not make real external references an error."""
    external = FLATLAND_OPF.replace(
        b'href="../nav.xhtml"', b'href="https://example.invalid/nav.xhtml"')
    package = tmp_path / "external.kepub"
    _write_package(package, opf=external)

    result = _normalizer()(package)

    # nothing escapes the OPF dir any more, so this is a clean no-op, not a failure
    assert result is False


@pytest.mark.unit
def test_an_archive_that_decompresses_past_the_bound_is_refused(tmp_path, monkeypatch, caplog):
    """MemoryError is not an Exception, so the module's own catch cannot recover
    from exhausting the conversion worker. Books are user-uploadable, so this
    bound is reachable by input we do not control. Checked BEFORE the read.
    """
    from cps.services import kepub_package_normalizer as mod

    package = tmp_path / "big.kepub"
    _write_package(package)
    original = package.read_bytes()
    monkeypatch.setattr(mod, "MAX_TOTAL_UNCOMPRESSED_BYTES", 8)  # smaller than any real book

    with caplog.at_level("WARNING"):
        result = mod.normalize_kepub_package(package)

    assert result is None
    assert package.read_bytes() == original
    assert any("decompresses to more than" in r.getMessage() for r in caplog.records)


@pytest.mark.unit
@pytest.mark.parametrize("probe", [False, True], ids=["normalizer", "repair-probe"])
def test_total_size_is_rejected_before_any_member_read_or_crc_scan(
    tmp_path, monkeypatch, caplog, probe,
):
    from cps.services import kepub_package_normalizer as mod

    package = tmp_path / "early-bound.kepub"
    _write_package(package)
    reads = []
    crc_scans = []
    real_zip = zipfile.ZipFile

    class NoEarlyReadZipFile(real_zip):
        def read(self, *args, **kwargs):
            reads.append(args[0] if args else None)
            raise AssertionError("member read happened before total-size rejection")

        def testzip(self):
            crc_scans.append(True)
            raise AssertionError("CRC scan happened before total-size rejection")

    monkeypatch.setattr(mod, "MAX_TOTAL_UNCOMPRESSED_BYTES", 8)
    monkeypatch.setattr(mod.zipfile, "ZipFile", NoEarlyReadZipFile)

    with caplog.at_level("WARNING"):
        result = (mod.kepub_package_needs_normalization(package) if probe
                  else mod.normalize_kepub_package(package))

    if probe:
        assert result.status == mod.PROBE_UNSUPPORTED
    else:
        assert result is None
    assert reads == []
    assert crc_scans == []
    assert "decompresses to more than" in caplog.text


@pytest.mark.unit
@pytest.mark.parametrize(
    "member, limit_name, expected",
    [
        ("META-INF/container.xml", "MAX_CONTAINER_XML_BYTES", "container.xml"),
        ("OPS/epb.opf", "MAX_PACKAGE_DOCUMENT_BYTES", "package document"),
    ],
)
def test_structural_members_have_strict_per_entry_bounds(
    tmp_path, monkeypatch, caplog, member, limit_name, expected,
):
    from cps.services import kepub_package_normalizer as mod

    package = tmp_path / "structural-bound.kepub"
    _write_package(package)
    monkeypatch.setattr(mod, limit_name, 8, raising=False)

    with caplog.at_level("WARNING"):
        assert mod.normalize_kepub_package(package) is None
    assert expected in caplog.text


@pytest.mark.unit
def test_repair_probe_applies_the_opf_per_entry_bound(tmp_path, monkeypatch, caplog):
    from cps.services import kepub_package_normalizer as mod

    package = tmp_path / "probe-opf-bound.kepub"
    _write_package(package)
    monkeypatch.setattr(mod, "MAX_PACKAGE_DOCUMENT_BYTES", 8, raising=False)

    with caplog.at_level("WARNING"):
        inspection = mod.kepub_package_needs_normalization(package)
        assert inspection.status == mod.PROBE_UNSUPPORTED
    assert "package document" in caplog.text


@pytest.mark.unit
def test_repair_probe_classifies_io_failure_as_retryable(tmp_path, monkeypatch):
    from cps.services import kepub_package_normalizer as mod

    package = tmp_path / "temporarily-unreadable.kepub"
    package.write_bytes(b"placeholder")

    def unavailable(_path):
        raise PermissionError("network share is temporarily unavailable")

    monkeypatch.setattr(mod.zipfile, "ZipFile", unavailable)

    inspection = mod.kepub_package_needs_normalization(package)

    assert inspection.status == mod.PROBE_RETRYABLE
    assert "temporarily unavailable" in inspection.error_message


@pytest.mark.unit
def test_kepubify_conversion_RE_splits_an_annotated_book_that_was_already_split(
        tmp_path, monkeypatch):
    """The guard's second clause, which the convert path had no test for.

    "Never split an annotated book" is the wrong rule when the STORED package is
    already one of ours. Piece naming is deterministic, so re-splitting the same
    source reproduces the exact member names the existing annotations are
    anchored to -- while withholding the split deletes the very files those
    anchors name. Splitting preserves them; not splitting strands them.

    The upload path has covered this since F-bbd10e
    (test_1715_uploaded_kepub_is_normalized.py ::
    test_an_annotated_book_that_was_ALREADY_split_is_split_again). The
    conversion path carries the identical clause at cps/tasks/convert.py and had
    only the plain "annotated => do not split" case, so simplifying the
    expression to `may_split = not self._book_has_annotations()` -- the obvious
    reading -- left every conversion test green while silently stranding the
    anchors of every annotated, already-split book.

    The stored package here is a REAL splitter output, not a mock: the assertion
    exercises `package_was_split_by_us` itself rather than a stand-in for it.
    """
    from types import SimpleNamespace

    import cps.helper  # noqa: F401 - establish the application's normal import order
    from cps.tasks import convert

    book_path = tmp_path / "book"
    (tmp_path / "book.epub").write_bytes(b"source")
    _write_splittable_package(tmp_path / "book.kepub.epub")

    # The already-stored package: genuinely produced by our own splitter, so
    # `package_was_split_by_us` answers True on its real spine check.
    destination = tmp_path / "book.kepub"
    _write_splittable_package(destination)
    _normalizer()(str(destination), split_chapters=True)

    from cps.services.kepub_spine_splitter import package_was_split_by_us
    assert package_was_split_by_us(str(destination)), (
        "fixture is wrong: the stored package is not one the splitter produced, "
        "so this test could not tell the two branches apart")

    process = SimpleNamespace(returncode=0)
    monkeypatch.setattr(convert.config, "config_embed_metadata", False, raising=False)
    monkeypatch.setattr(convert.config, "config_binariesdir", "", raising=False)
    monkeypatch.setattr(convert.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(convert, "process_open", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(convert, "stream_process_output", lambda *_args, **_kwargs: [])
    _patch_annotation_store(monkeypatch, convert, annotations=1)
    task = convert.TaskConvert(str(book_path), 1, "convert", {}, None)

    check, error = task._convert_kepubify(str(book_path), ".epub", ".kepub")

    assert check == 0, error
    assert _ncx_sources(destination) == [
        "chapter-split-1.xhtml",
        "chapter-split-2.xhtml",
    ], ("an annotated book whose stored KEPUB was already split came back "
        "unsplit; the chapter files its highlights are anchored to no longer "
        "exist, so they would stop rendering on the device")


@pytest.mark.unit
def test_kepubify_conversion_keeps_an_existing_UNSPLIT_annotated_book_unsplit(
        tmp_path, monkeypatch):
    """The convert boundary must use the detector's False result, not existence.

    The positive partner above proves a real splitter output is accepted. This
    case supplies the missing other half: the destination exists, but its spine
    is ordinary and unsplit. Merely checking ``os.path.exists(destination)`` --
    or replacing ``package_was_split_by_us`` with an always-True predicate --
    would split the replacement and strand annotations anchored to
    ``chapter.xhtml``.

    Wrap the real detector and record its argument so the assertion proves the
    decision was made at the conversion boundary, against the stored package.
    """
    from types import SimpleNamespace

    import cps.helper  # noqa: F401 - establish the application's normal import order
    from cps.services import kepub_spine_splitter
    from cps.tasks import convert

    book_path = tmp_path / "book"
    (tmp_path / "book.epub").write_bytes(b"source")
    _write_splittable_package(tmp_path / "book.kepub.epub")

    destination = tmp_path / "book.kepub"
    _write_splittable_package(destination)
    assert _ncx_sources(destination) == [
        "chapter.xhtml#one",
        "chapter.xhtml#two",
    ], "fixture is wrong: the stored package is not an ordinary unsplit KEPUB"

    real_detector = kepub_spine_splitter.package_was_split_by_us
    detector_calls = []

    def record_detector_call(path):
        detector_calls.append(path)
        return real_detector(path)

    monkeypatch.setattr(
        kepub_spine_splitter, "package_was_split_by_us", record_detector_call)

    process = SimpleNamespace(returncode=0)
    monkeypatch.setattr(convert.config, "config_embed_metadata", False, raising=False)
    monkeypatch.setattr(convert.config, "config_binariesdir", "", raising=False)
    monkeypatch.setattr(convert.config, "config_kepubifypath", "/bin/kepubify", raising=False)
    monkeypatch.setattr(convert, "process_open", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(convert, "stream_process_output", lambda *_args, **_kwargs: [])
    _patch_annotation_store(monkeypatch, convert, annotations=1)
    task = convert.TaskConvert(str(book_path), 1, "convert", {}, None)

    check, error = task._convert_kepubify(str(book_path), ".epub", ".kepub")

    assert check == 0, error
    assert detector_calls == [str(destination)], (
        "conversion did not ask the real detector about the stored package")
    assert _ncx_sources(destination) == [
        "chapter.xhtml#one",
        "chapter.xhtml#two",
    ], ("an annotated book whose stored KEPUB was unsplit came back split; "
        "its highlights were anchored to the chapter.xhtml spine document")
