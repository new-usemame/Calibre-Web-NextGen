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


def _write_series_kepub(path, include_series=True):
    series_metadata = b""
    if include_series:
        series_metadata = b"""    <meta name="calibre:series" content="Residual Series"/>
    <meta name="calibre:series_index" content="4.0"/>
"""
    package = b"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf"
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
</package>""" % series_metadata
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
    return {
        element.get("name") or element.get("property")
        for element in ET.fromstring(package).iter()
        if element.tag.rsplit("}", 1)[-1] == "meta"
        and (element.get("name") or element.get("property"))
        in {"calibre:series", "calibre:series_index"}
    }


def _kobo_span_counts(contents):
    return {
        name: content.count(b'class="koboSpan')
        for name, content in contents.items()
    }


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
