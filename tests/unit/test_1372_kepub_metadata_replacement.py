# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""Regression coverage for replacement semantics in kepub metadata enforcement.

The argv assertions pin the intended overrides, not proof that every Calibre
flag reaches the file end-to-end. In particular, series clearing is not yet
observable: ``ebook-meta --series ""`` leaves an existing ``calibre:series``
meta in a real kepubify KEPUB, so a green test here must not be read as proof
that series removal works.
"""

import atexit
import importlib.util
import shutil
import sys
import tempfile
import types
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


def _capture_kepub_command(module, monkeypatch, book_dir, opf):
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
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module, "Book", _FakeBook)
    monkeypatch.setattr(module.subprocess, "run", _fake_run)
    monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    monkeypatch.setattr(module.Enforcer, "replace_old_metadata", lambda *args: None)
    monkeypatch.setattr(module.Enforcer, "empty_metadata_temp", lambda *args: None)
    monkeypatch.setattr(
        module.Enforcer,
        "_recalculate_checksum_after_modification",
        lambda *args: None,
    )
    monkeypatch.setattr(
        module.Enforcer,
        "_reset_book_dir_ownership",
        staticmethod(lambda *args: None),
    )

    enforcer = object.__new__(module.Enforcer)
    enforcer.supported_formats = ["kepub"]
    enforcer.enforce_cover(str(book_dir))

    assert len(calls) == 1
    return calls[0]


def _option_value(cmd, option):
    return cmd[cmd.index(option) + 1]


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

    assert cmd[:4] == [
        "ebook-meta",
        str(book_dir / "book.kepub"),
        "--from-opf",
        str(opf),
    ]
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
def test_malformed_opf_falls_back_to_plain_from_opf_command(
    enforcer_module, tmp_path, monkeypatch, capsys
):
    book_dir = tmp_path / "Author" / "Title (5)"
    book_dir.mkdir(parents=True)
    kepub = book_dir / "book.kepub"
    kepub.write_bytes(b"PK\x03\x04stub")
    opf = tmp_path / "metadata.opf"
    opf.write_text("<package><metadata>", encoding="utf-8")

    cmd = _capture_kepub_command(
        enforcer_module, monkeypatch, book_dir, opf
    )

    assert cmd == ["ebook-meta", str(kepub), "--from-opf", str(opf)]
    assert "[cover-metadata-enforcer] Warning:" in capsys.readouterr().out
