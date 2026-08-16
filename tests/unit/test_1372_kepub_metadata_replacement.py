# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression coverage for replacement semantics in kepub metadata enforcement.

The argv assertions pin the intended overrides. Series clearing is separately
asserted against the resulting package document in a real KEPUB archive because
``ebook-meta --series ""`` alone leaves existing series metadata behind.
"""

import atexit
import importlib.util
import shutil
import stat
import sys
import tempfile
import types
import zipfile
from xml.etree import ElementTree as ET
from pathlib import Path

import pytest
from lxml import etree

from tests.fixtures.kepub_fixture import build_calibre_epub3_series_kepub


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "cover_enforcer.py"


def _load_module():
    stub = types.ModuleType("cwa_db")

    class _StubDB:  # pragma: no cover - only needed while importing the script
        def __init__(self, *args, **kwargs):
            self.cwa_settings = {"auto_metadata_enforcement": 1}

    stub.CWA_DB = _StubDB
    sentinel = object()
    previous = sys.modules.get("cwa_db", sentinel)
    sys.modules["cwa_db"] = stub

    private_tmp = tempfile.mkdtemp(prefix="kepub_metadata_replacement_test_")
    real_gettempdir = tempfile.gettempdir
    tempfile.gettempdir = lambda: private_tmp
    try:
        spec = importlib.util.spec_from_file_location(
            "cover_enforcer_metadata_replacement_under_test", SCRIPT
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        tempfile.gettempdir = real_gettempdir
        if previous is sentinel:
            sys.modules.pop("cwa_db", None)
        else:
            sys.modules["cwa_db"] = previous

    atexit.unregister(module.removeLock)
    shutil.rmtree(private_tmp, ignore_errors=True)
    return module


@pytest.fixture(scope="module")
def enforcer_module():
    return _load_module()


@pytest.mark.unit
def test_cover_enforcer_import_does_not_require_kepub_normalizer(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "cps.services.kepub_package_normalizer", None
    )

    try:
        module = _load_module()
    except ModuleNotFoundError as error:
        pytest.fail(
            "cover_enforcer import must not require the KEPUB normalizer: "
            f"{error}"
        )

    assert module.Enforcer is not None


def _capture_kepub_command(
    module,
    monkeypatch,
    book_dir,
    opf,
    expected_call_count=1,
    subprocess_result=None,
    subprocess_exception=None,
    checksum_calls=None,
):
    calls = []

    class _FakeBook:
        def __init__(self, directory, file_path):
            self.file_path = file_path
            self.file_format = "kepub"
            self.cover_path = str(Path(directory) / "cover.jpg")
            self.old_metadata_path = str(Path(directory) / "metadata.opf")
            self.new_metadata_path = str(opf)
            self.book_id = "3"
            self.title_author = "Title - Author"

    def _fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if subprocess_exception is not None:
            raise subprocess_exception
        return subprocess_result or types.SimpleNamespace(
            returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(module, "Book", _FakeBook)
    monkeypatch.setattr(module.subprocess, "run", _fake_run)
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    monkeypatch.setattr(module.Enforcer, "replace_old_metadata", lambda *args: None)
    monkeypatch.setattr(module.Enforcer, "empty_metadata_temp", lambda *args: None)
    if checksum_calls is None:
        monkeypatch.setattr(
            module.Enforcer,
            "_recalculate_checksum_after_modification",
            lambda *args: None,
        )
    else:
        monkeypatch.setattr(
            module.Enforcer,
            "_recalculate_checksum_after_modification",
            lambda *args: checksum_calls.append(args),
        )
    monkeypatch.setattr(
        module.Enforcer,
        "_reset_book_dir_ownership",
        staticmethod(lambda *args: None),
    )

    enforcer = object.__new__(module.Enforcer)
    enforcer.supported_formats = ["kepub"]
    enforcer.enforce_cover(str(book_dir))

    assert len(calls) == expected_call_count
    return calls[0] if calls else None


def _option_value(cmd, option):
    return cmd[cmd.index(option) + 1]


def _write_series_kepub(path, include_series=True, package_prolog=b""):
    series_metadata = b""
    if include_series:
        series_metadata = b"""    <meta name="calibre:series" content="Residual Series"/>
    <meta name="calibre:series_index" content="4.0"/>
"""
    package = b"""<?xml version="1.0" encoding="UTF-8"?>
%s<package xmlns="http://www.idpf.org/2007/opf"
         xmlns:dc="http://purl.org/dc/elements/1.1/"
         unique-identifier="book-id" version="3.0">
  <metadata>
    <dc:identifier id="book-id">issue-1372</dc:identifier>
    <dc:title>Title</dc:title>
    <dc:creator>Author</dc:creator>
    <dc:language>eng</dc:language>
%s
  </metadata>
  <manifest>
    <item id="chapter" href="chapter.xhtml"
          media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>""" % (package_prolog, series_metadata)
    container = b"""<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
           version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf"
              media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
    chapter = b"""<html xmlns="http://www.w3.org/1999/xhtml"><body><p>
<span class="koboSpan" id="kobo.1.1">One</span>
<span class="koboSpan extra" id="kobo.1.2">Two</span>
<span class="koboSpan" id="kobo.1.3">Three</span>
</p></body></html>"""

    with zipfile.ZipFile(path, "w") as archive:
        archive.comment = b"issue-1372 archive comment"
        archive.writestr(
            "mimetype",
            b"application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr("OEBPS/chapter.xhtml", chapter)


def _archive_contents(path):
    with zipfile.ZipFile(path) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def _archive_comment(path):
    with zipfile.ZipFile(path) as archive:
        return archive.comment


def _write_exported_opf_without_series(path):
    path.write_text(
        "<package xmlns=\"http://www.idpf.org/2007/opf\" "
        "xmlns:dc=\"http://purl.org/dc/elements/1.1/\">"
        "<metadata>"
        "<dc:title>Title</dc:title>"
        "<dc:creator>Author</dc:creator>"
        "<dc:language>eng</dc:language>"
        "</metadata>"
        "</package>",
        encoding="utf-8",
    )


def _series_metadata(package):
    metas = [
        element
        for element in ET.fromstring(package).iter()
        if isinstance(element.tag, str)
        and element.tag.rsplit("}", 1)[-1] == "meta"
    ]
    found = {
        element.get("name")
        for element in metas
        if element.get("name") in {"calibre:series", "calibre:series_index"}
    }
    collections = {
        element.get("id"): element
        for element in metas
        if element.get("property") == "belongs-to-collection"
        and element.get("id")
    }
    series_ids = {
        element.get("refines", "").removeprefix("#")
        for element in metas
        if element.get("property") == "collection-type"
        and "".join(element.itertext()).strip().lower() == "series"
    } & collections.keys()
    for element in metas:
        property_name = element.get("property")
        refined_id = element.get("refines", "").removeprefix("#")
        if element.get("id") in series_ids and property_name == "belongs-to-collection":
            found.add(property_name)
        elif refined_id in series_ids and property_name in {
            "collection-type", "group-position"
        }:
            found.add(property_name)
    return found


def _metadata_properties(package):
    return {
        element.get("property")
        for element in ET.fromstring(package).iter()
        if isinstance(element.tag, str)
        and element.tag.rsplit("}", 1)[-1] == "meta"
        and element.get("property")
    }


def _kobo_span_counts(contents):
    return {
        name: content.count(b'class="koboSpan')
        for name, content in contents.items()
    }


def _remove_epub3_series(package):
    metas = package.xpath("//*[local-name()='meta']")
    collection_ids = {
        element.get("id")
        for element in metas
        if element.get("property") == "belongs-to-collection"
        and element.get("id")
    }
    series_ids = {
        element.get("refines", "").removeprefix("#")
        for element in metas
        if element.get("property") == "collection-type"
        and "".join(element.itertext()).strip().lower() == "series"
    } & collection_ids

    changed = False
    for parent in package.iter():
        for child in list(parent):
            property_name = child.get("property") if isinstance(child.tag, str) else None
            refined_id = child.get("refines", "").removeprefix("#") if property_name else ""
            if (
                property_name == "belongs-to-collection"
                and child.get("id") in series_ids
            ) or (
                property_name in {"collection-type", "group-position"}
                and refined_id in series_ids
            ):
                parent.remove(child)
                changed = True
    return changed


@pytest.mark.unit
@pytest.mark.parametrize(
    ("subjects", "expected_tags"),
    [
        ("", ""),
        ("<dc:subject>kept</dc:subject>", "kept"),
    ],
)
def test_kepub_metadata_fields_replace_instead_of_merge(
    enforcer_module, tmp_path, monkeypatch, subjects, expected_tags
):
    book_dir = tmp_path / "Author" / "Title (3)"
    book_dir.mkdir(parents=True)
    (book_dir / "book.kepub").write_bytes(b"PK\x03\x04stub")
    opf = tmp_path / "metadata.opf"
    opf.write_text(
        "<package xmlns:dc=\"http://purl.org/dc/elements/1.1/\">"
        "<metadata>"
        "<dc:title>Title</dc:title>"
        "<dc:creator>Author</dc:creator>"
        "<dc:language>eng</dc:language>"
        f"{subjects}"
        "</metadata>"
        "</package>",
        encoding="utf-8",
    )

    cmd = _capture_kepub_command(
        enforcer_module, monkeypatch, book_dir, opf
    )

    assert cmd[0] == "ebook-meta"
    assert cmd[1].endswith(".kepub")
    assert cmd[1] != str(book_dir / "book.kepub")
    assert cmd[2:4] == ["--from-opf", str(opf)]
    assert _option_value(cmd, "--series") == ""
    assert _option_value(cmd, "--tags") == expected_tags
    assert _option_value(cmd, "--publisher") == ""
    assert _option_value(cmd, "--language") == "eng"
    assert _option_value(cmd, "--authors") == "Author"
    assert _option_value(cmd, "--title") == "Title"
    assert _option_value(cmd, "--comments") == ""
    assert "--date" not in cmd
    assert "--index" not in cmd
    assert "--rating" not in cmd


@pytest.mark.unit
def test_kepub_metadata_values_are_read_from_the_exported_opf(
    enforcer_module, tmp_path, monkeypatch
):
    book_dir = tmp_path / "Authors" / "Title (4)"
    book_dir.mkdir(parents=True)
    (book_dir / "book.kepub").write_bytes(b"PK\x03\x04stub")
    opf = tmp_path / "metadata.opf"
    opf.write_text(
        "<package xmlns:dc=\"http://purl.org/dc/elements/1.1/\">"
        "<metadata>"
        "<dc:title>New Title</dc:title>"
        "<dc:creator>First Author</dc:creator>"
        "<dc:creator>Second Author</dc:creator>"
        "<dc:subject>kept</dc:subject>"
        "<dc:subject>new</dc:subject>"
        "<dc:publisher>Publisher</dc:publisher>"
        "<dc:description>Comments</dc:description>"
        "<dc:language>eng</dc:language>"
        "<dc:language>fra</dc:language>"
        "<dc:date>2026-08-15</dc:date>"
        "<meta name=\"calibre:series\" content=\"New Series\"/>"
        "<meta name=\"calibre:series_index\" content=\"7\"/>"
        "<meta name=\"calibre:rating\" content=\"4.0\"/>"
        "</metadata>"
        "</package>",
        encoding="utf-8",
    )

    cmd = _capture_kepub_command(
        enforcer_module, monkeypatch, book_dir, opf
    )

    assert _option_value(cmd, "--series") == "New Series"
    assert _option_value(cmd, "--index") == "7"
    assert _option_value(cmd, "--tags") == "kept, new"
    assert _option_value(cmd, "--publisher") == "Publisher"
    assert _option_value(cmd, "--comments") == "Comments"
    assert _option_value(cmd, "--language") == "eng, fra"
    assert _option_value(cmd, "--authors") == "First Author & Second Author"
    assert _option_value(cmd, "--title") == "New Title"
    assert _option_value(cmd, "--date") == "2026-08-15"
    assert _option_value(cmd, "--rating") == "4.0"


@pytest.mark.unit
def test_clearing_series_removes_residual_kepub_package_metadata(
    enforcer_module, tmp_path, monkeypatch
):
    book_dir = tmp_path / "Author" / "Title (1372)"
    book_dir.mkdir(parents=True)
    kepub = book_dir / "book.kepub"
    _write_series_kepub(kepub)
    kepub.chmod(0o640)
    exported_opf = tmp_path / "library.opf"
    _write_exported_opf_without_series(exported_opf)
    before = _archive_contents(kepub)

    _capture_kepub_command(
        enforcer_module, monkeypatch, book_dir, exported_opf
    )

    after = _archive_contents(kepub)
    assert _series_metadata(before["OEBPS/content.opf"]) == {
        "calibre:series",
        "calibre:series_index",
    }
    assert _series_metadata(after["OEBPS/content.opf"]) == set()
    assert _kobo_span_counts(before) == _kobo_span_counts(after)
    assert _kobo_span_counts(after)["OEBPS/chapter.xhtml"] == 3


@pytest.mark.unit
def test_clearing_real_kepubify_epub3_series_survives_calibre_comments(
    enforcer_module, tmp_path, monkeypatch
):
    book_dir = tmp_path / "Author" / "Real kepubify shape (1372)"
    book_dir.mkdir(parents=True)
    kepub = build_calibre_epub3_series_kepub(book_dir / "book.kepub")
    exported_opf = tmp_path / "library.opf"
    _write_exported_opf_without_series(exported_opf)
    before = _archive_contents(kepub)

    _capture_kepub_command(
        enforcer_module, monkeypatch, book_dir, exported_opf
    )

    after = _archive_contents(kepub)
    assert before["OEBPS/content.opf"].count(b"<!--") == 2
    assert _series_metadata(before["OEBPS/content.opf"]) == {
        "belongs-to-collection",
        "collection-type",
        "group-position",
    }
    assert _series_metadata(after["OEBPS/content.opf"]) == set()
    assert after["OEBPS/content.opf"].count(b"<!--") == 2
    assert {"title-type", "file-as", "role"} <= _metadata_properties(
        after["OEBPS/content.opf"]
    )
    assert b'id="id-6"' in after["OEBPS/content.opf"]
    assert b'refines="#id-6"' in after["OEBPS/content.opf"]
    assert _kobo_span_counts(before) == _kobo_span_counts(after)
    for name in before.keys() - {"OEBPS/content.opf"}:
        assert after[name] == before[name]


@pytest.mark.unit
def test_package_rewrite_clears_series_from_real_kepubify_shape(tmp_path):
    from cps.services.kepub_package_normalizer import rewrite_package_document

    kepub = build_calibre_epub3_series_kepub(tmp_path / "real-shape.kepub")
    kepub.chmod(0o640)
    before = _archive_contents(kepub)
    before_comment = _archive_comment(kepub)
    before_mode = stat.S_IMODE(kepub.stat().st_mode)

    assert b'href="../nav.xhtml"' in before["OEBPS/content.opf"]
    assert "nav.xhtml" in before
    assert sum(_kobo_span_counts(before).values()) == 3
    assert rewrite_package_document(kepub, _remove_epub3_series) is True

    after = _archive_contents(kepub)
    assert _series_metadata(after["OEBPS/content.opf"]) == set()
    assert b'href="../nav.xhtml"' in after["OEBPS/content.opf"]
    assert set(after) == set(before)
    assert len(after) == len(before)
    assert _kobo_span_counts(after) == _kobo_span_counts(before)
    for name in before.keys() - {"OEBPS/content.opf"}:
        assert after[name] == before[name]
    assert _archive_comment(kepub) == before_comment
    assert stat.S_IMODE(kepub.stat().st_mode) == before_mode


@pytest.mark.unit
def test_enforcer_strip_clears_series_from_real_kepubify_shape(
    enforcer_module, tmp_path
):
    kepub = build_calibre_epub3_series_kepub(tmp_path / "enforcer-entry.kepub")
    before = _archive_contents(kepub)

    assert enforcer_module._strip_kepub_series_metadata(kepub) is True

    after = _archive_contents(kepub)
    assert _series_metadata(after["OEBPS/content.opf"]) == set()
    assert b'href="../nav.xhtml"' in after["OEBPS/content.opf"]
    assert set(after) == set(before)
    assert _kobo_span_counts(after) == _kobo_span_counts(before)
    for name in before.keys() - {"OEBPS/content.opf"}:
        assert after[name] == before[name]


@pytest.mark.unit
def test_package_rewrite_rejects_new_escaping_manifest_href(tmp_path):
    from cps.services.kepub_package_normalizer import rewrite_package_document

    kepub = tmp_path / "introduced-escape.kepub"
    _write_series_kepub(kepub)
    with zipfile.ZipFile(kepub, "a") as archive:
        archive.writestr("introduced.xhtml", b"<html/>")
    original = kepub.read_bytes()

    def add_escaping_item(package):
        manifest = package.xpath("//*[local-name()='manifest']")[0]
        etree.SubElement(
            manifest,
            "{http://www.idpf.org/2007/opf}item",
            id="introduced-escape",
            href="../introduced.xhtml",
            attrib={"media-type": "application/xhtml+xml"},
        )
        return True

    assert rewrite_package_document(kepub, add_escaping_item) is None
    assert kepub.read_bytes() == original
    assert list(tmp_path.glob(".introduced-escape.kepub.*.package-rewrite.tmp")) == []


@pytest.mark.unit
def test_package_rewrite_rejects_duplicate_escaping_manifest_href(tmp_path):
    from cps.services.kepub_package_normalizer import rewrite_package_document

    kepub = build_calibre_epub3_series_kepub(tmp_path / "duplicate-escape.kepub")
    original = kepub.read_bytes()

    def duplicate_escaping_item(package):
        manifest = package.xpath("//*[local-name()='manifest']")[0]
        etree.SubElement(
            manifest,
            "{http://www.idpf.org/2007/opf}item",
            id="duplicate-nav",
            href="../nav.xhtml",
            attrib={
                "media-type": "application/xhtml+xml",
                "properties": "nav",
            },
        )
        return True

    assert rewrite_package_document(kepub, duplicate_escaping_item) is None
    assert kepub.read_bytes() == original
    assert list(tmp_path.glob(".duplicate-escape.kepub.*.package-rewrite.tmp")) == []


@pytest.mark.unit
def test_package_rewrite_rejects_reassigned_escaping_manifest_href(tmp_path):
    from cps.services.kepub_package_normalizer import rewrite_package_document

    kepub = build_calibre_epub3_series_kepub(tmp_path / "reassigned-escape.kepub")
    original = kepub.read_bytes()

    def reassign_escaping_item(package):
        items = {
            item.get("id"): item
            for item in package.xpath("//*[local-name()='manifest']/*[local-name()='item']")
        }
        items["nav"].set("href", "chapter.xhtml")
        items["chapter"].set("href", "../nav.xhtml")
        return True

    assert rewrite_package_document(kepub, reassign_escaping_item) is None
    assert kepub.read_bytes() == original
    assert list(tmp_path.glob(".reassigned-escape.kepub.*.package-rewrite.tmp")) == []


@pytest.mark.unit
def test_series_clear_preserves_package_doctype_and_processing_instruction(
    enforcer_module, tmp_path
):
    kepub = tmp_path / "package-prolog.kepub"
    _write_series_kepub(
        kepub,
        package_prolog=(
            b"<!DOCTYPE package>\n"
            b"<?calibre-marker preserve-this?>\n"
        ),
    )
    before = _archive_contents(kepub)

    assert b"<!DOCTYPE package>" in before["OEBPS/content.opf"]
    assert b"<?calibre-marker preserve-this?>" in before["OEBPS/content.opf"]
    assert enforcer_module._strip_kepub_series_metadata(kepub) is True

    after = _archive_contents(kepub)
    assert _series_metadata(after["OEBPS/content.opf"]) == set()
    assert b"<!DOCTYPE package>" in after["OEBPS/content.opf"]
    assert b"<?calibre-marker preserve-this?>" in after["OEBPS/content.opf"]
    assert set(after) == set(before)
    for name in before.keys() - {"OEBPS/content.opf"}:
        assert after[name] == before[name]


@pytest.mark.unit
def test_normalizer_rejects_output_that_still_escapes(
    tmp_path, monkeypatch, caplog
):
    from cps.services import kepub_package_normalizer as normalizer

    kepub = build_calibre_epub3_series_kepub(tmp_path / "normalizer-postcondition.kepub")
    original = kepub.read_bytes()
    real_rewritten_entries = normalizer._rewritten_entries

    def keep_escaping_package_document(infos, contents, relocations):
        entries = real_rewritten_entries(infos, contents, relocations)
        return [
            (info, contents["OEBPS/content.opf"])
            if info.filename == "OEBPS/content.opf"
            else (info, content)
            for info, content in entries
        ]

    monkeypatch.setattr(
        normalizer, "_rewritten_entries", keep_escaping_package_document
    )

    assert normalizer.normalize_kepub_package(kepub) is None
    assert kepub.read_bytes() == original
    assert "rewritten manifest still escapes the OPF directory" in caplog.text
    assert list(tmp_path.glob(".normalizer-postcondition.kepub.*.normalize.tmp")) == []


@pytest.mark.unit
def test_series_rewrite_preserves_other_members_comment_and_mode(
    enforcer_module, tmp_path, monkeypatch
):
    book_dir = tmp_path / "Author" / "Archive preservation (1372)"
    book_dir.mkdir(parents=True)
    kepub = book_dir / "book.kepub"
    _write_series_kepub(kepub)
    kepub.chmod(0o640)
    exported_opf = tmp_path / "library.opf"
    _write_exported_opf_without_series(exported_opf)
    before = _archive_contents(kepub)
    before_comment = _archive_comment(kepub)
    before_mode = stat.S_IMODE(kepub.stat().st_mode)

    _capture_kepub_command(
        enforcer_module, monkeypatch, book_dir, exported_opf
    )

    after = _archive_contents(kepub)
    assert _series_metadata(after["OEBPS/content.opf"]) == set()
    assert set(before) == set(after)
    for name in before.keys() - {"OEBPS/content.opf"}:
        assert after[name] == before[name]
    assert _kobo_span_counts(before) == _kobo_span_counts(after)
    assert _archive_comment(kepub) == before_comment
    assert stat.S_IMODE(kepub.stat().st_mode) == before_mode


@pytest.mark.unit
def test_unchanged_series_does_not_stage_or_replace_kepub(
    enforcer_module, tmp_path, monkeypatch
):
    book_dir = tmp_path / "Author" / "Title (1373)"
    book_dir.mkdir(parents=True)
    kepub = book_dir / "book.kepub"
    _write_series_kepub(kepub)
    exported_opf = tmp_path / "library-with-series.opf"
    exported_opf.write_text(
        "<package xmlns:dc=\"http://purl.org/dc/elements/1.1/\">"
        "<metadata>"
        "<dc:title>Title</dc:title>"
        "<dc:creator>Author</dc:creator>"
        "<dc:language>eng</dc:language>"
        "<meta name=\"calibre:series\" content=\"Residual Series\"/>"
        "<meta name=\"calibre:series_index\" content=\"4.0\"/>"
        "</metadata>"
        "</package>",
        encoding="utf-8",
    )
    original = kepub.read_bytes()
    real_mkstemp = enforcer_module.tempfile.mkstemp

    def reject_metadata_stage(*args, **kwargs):
        if kwargs.get("prefix", "").endswith(".metadata-"):
            raise AssertionError("unchanged series must not stage the KEPUB")
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(enforcer_module.tempfile, "mkstemp", reject_metadata_stage)
    monkeypatch.setattr(
        enforcer_module.os,
        "replace",
        lambda *args: pytest.fail("unchanged series must not replace the KEPUB"),
    )

    cmd = _capture_kepub_command(
        enforcer_module, monkeypatch, book_dir, exported_opf
    )

    assert cmd[1] == str(kepub)
    assert kepub.read_bytes() == original


@pytest.mark.unit
def test_series_strip_is_byte_identical_noop_when_kepub_has_no_series(
    enforcer_module, tmp_path, monkeypatch
):
    strip_series = getattr(enforcer_module, "_strip_kepub_series_metadata", None)
    assert strip_series is not None, "series-clearing helper is missing"
    kepub = tmp_path / "already-clear.kepub"
    _write_series_kepub(kepub, include_series=False)
    original = kepub.read_bytes()

    def reject_replace(*args, **kwargs):
        raise AssertionError("an already-clear package must not be replaced")

    monkeypatch.setattr(enforcer_module.os, "replace", reject_replace)

    assert strip_series(kepub) is False
    assert kepub.read_bytes() == original


@pytest.mark.unit
def test_enforcer_imports_only_public_kepub_package_rewriter():
    source = SCRIPT.read_text(encoding="utf-8")
    import_block = source.split(
        "from cps.services.kepub_package_normalizer import", 1
    )[1].split("\n", 1)[0]

    assert import_block.strip() == "rewrite_package_document"
    assert "_package_document_path" not in source
    assert "_read_bounded_member" not in source
    assert "_validate_rewritten_archive" not in source


@pytest.mark.unit
def test_malformed_opf_preserves_kepub_without_running_writer(
    enforcer_module, tmp_path, monkeypatch, capsys
):
    book_dir = tmp_path / "Author" / "Title (5)"
    book_dir.mkdir(parents=True)
    kepub = book_dir / "book.kepub"
    kepub.write_bytes(b"PK\x03\x04stub")
    opf = tmp_path / "metadata.opf"
    opf.write_text("<package><metadata>", encoding="utf-8")

    before = kepub.read_bytes()
    cmd = _capture_kepub_command(
        enforcer_module, monkeypatch, book_dir, opf, expected_call_count=0
    )

    assert cmd is None
    assert kepub.read_bytes() == before
    warning = capsys.readouterr().out
    assert "[cover-metadata-enforcer] Warning:" in warning
    assert "Title - Author" in warning
    assert "(kepub)" in warning
    assert "original file preserved" in warning


@pytest.mark.unit
def test_opf_without_metadata_element_preserves_kepub_without_running_writer(
    enforcer_module, tmp_path, monkeypatch, capsys
):
    book_dir = tmp_path / "Author" / "Title (missing metadata)"
    book_dir.mkdir(parents=True)
    kepub = book_dir / "book.kepub"
    _write_series_kepub(kepub)
    original = kepub.read_bytes()
    opf = tmp_path / "metadata.opf"
    opf.write_text("<package/>", encoding="utf-8")

    cmd = _capture_kepub_command(
        enforcer_module, monkeypatch, book_dir, opf, expected_call_count=0
    )

    output = capsys.readouterr().out
    assert cmd is None
    assert kepub.read_bytes() == original
    assert list(book_dir.glob(".*.metadata-*.kepub")) == []
    assert "Title - Author" in output
    assert "metadata element is missing" in output
    assert "original file preserved" in output


@pytest.mark.unit
def test_failed_series_rewrite_preserves_original_kepub(
    enforcer_module, tmp_path, monkeypatch, capsys
):
    book_dir = tmp_path / "Author" / "Title (6)"
    book_dir.mkdir(parents=True)
    kepub = book_dir / "book.kepub"
    original = b"not a valid KEPUB archive"
    kepub.write_bytes(original)
    opf = tmp_path / "metadata.opf"
    opf.write_text(
        "<package xmlns:dc=\"http://purl.org/dc/elements/1.1/\">"
        "<metadata>"
        "<dc:title>Title</dc:title>"
        "<dc:creator>Author</dc:creator>"
        "<dc:language>eng</dc:language>"
        "</metadata>"
        "</package>",
        encoding="utf-8",
    )

    checksum_calls = []
    _capture_kepub_command(
        enforcer_module,
        monkeypatch,
        book_dir,
        opf,
        checksum_calls=checksum_calls,
    )

    assert kepub.read_bytes() == original
    warning = capsys.readouterr().out
    assert "[cover-metadata-enforcer] Warning:" in warning
    assert "could not clear series" in warning
    assert "Title - Author" in warning
    assert "(kepub)" in warning
    assert "original file preserved" in warning
    assert "[cover-metadata-enforcer]: DONE:" not in warning
    assert checksum_calls == []
    assert list(book_dir.glob(".*.metadata-*.kepub")) == []


@pytest.mark.unit
def test_failed_ebook_meta_does_not_log_done_or_recalculate_checksum(
    enforcer_module, tmp_path, monkeypatch, capsys
):
    book_dir = tmp_path / "Author" / "Title (7)"
    book_dir.mkdir(parents=True)
    kepub = book_dir / "book.kepub"
    _write_series_kepub(kepub)
    original = kepub.read_bytes()
    opf = tmp_path / "metadata.opf"
    opf.write_text(
        "<package xmlns:dc=\"http://purl.org/dc/elements/1.1/\">"
        "<metadata><dc:title>Title</dc:title>"
        "<dc:creator>Author</dc:creator>"
        "<dc:language>eng</dc:language></metadata></package>",
        encoding="utf-8",
    )
    checksum_calls = []

    _capture_kepub_command(
        enforcer_module,
        monkeypatch,
        book_dir,
        opf,
        subprocess_result=types.SimpleNamespace(
            returncode=23, stdout="", stderr="writer failed"
        ),
        checksum_calls=checksum_calls,
    )

    output = capsys.readouterr().out
    assert kepub.read_bytes() == original
    assert list(book_dir.glob(".*.metadata-*.kepub")) == []
    assert "[cover-metadata-enforcer]: DONE:" not in output
    assert checksum_calls == []
    assert "Title - Author" in output
    assert "original file preserved" in output


@pytest.mark.unit
def test_timed_out_ebook_meta_preserves_original_and_removes_stage(
    enforcer_module, tmp_path, monkeypatch, capsys
):
    book_dir = tmp_path / "Author" / "Title (timeout)"
    book_dir.mkdir(parents=True)
    kepub = book_dir / "book.kepub"
    _write_series_kepub(kepub)
    original = kepub.read_bytes()
    opf = tmp_path / "metadata.opf"
    opf.write_text(
        "<package xmlns:dc=\"http://purl.org/dc/elements/1.1/\">"
        "<metadata><dc:title>Title</dc:title>"
        "<dc:creator>Author</dc:creator>"
        "<dc:language>eng</dc:language></metadata></package>",
        encoding="utf-8",
    )
    checksum_calls = []

    _capture_kepub_command(
        enforcer_module,
        monkeypatch,
        book_dir,
        opf,
        subprocess_exception=enforcer_module.subprocess.TimeoutExpired(
            cmd="ebook-meta", timeout=120
        ),
        checksum_calls=checksum_calls,
    )

    output = capsys.readouterr().out
    assert kepub.read_bytes() == original
    assert list(book_dir.glob(".*.metadata-*.kepub")) == []
    assert "Title - Author" in output
    assert "timed out" in output
    assert "original file preserved" in output
    assert "[cover-metadata-enforcer]: DONE:" not in output
    assert checksum_calls == []
