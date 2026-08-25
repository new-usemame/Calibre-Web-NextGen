# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioural coverage for the Kobo chapter-anchorability instrument."""
from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "measure_kobo_anchorable_chapters.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "measure_kobo_anchorable_chapters", SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _kepub(tmp_path, targets, *, name="Synthetic Book (1)"):
    book_dir = tmp_path / name
    book_dir.mkdir()
    path = book_dir / "book.kepub"
    links = "".join(
        f'<li><a href="{target}">Chapter {index}</a></li>'
        for index, target in enumerate(targets, 1)
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
      media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"
          properties="nav"/>
  </manifest>
  <spine><itemref idref="chapter"/></spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body>
  <h1 id="chapter-two">Chapter</h1><footer id="pg-footer-heading">Licence</footer>
</body></html>""",
        )
        archive.writestr(
            "OEBPS/nav.xhtml",
            f"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:epub="http://www.idpf.org/2007/ops"><body>
  <nav epub:type="toc"><ol>{links}</ol></nav>
</body></html>""",
        )
    return path


def test_import_with_no_argv_has_no_output_or_global_side_effects(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    original_path = list(sys.path)

    module = _module()

    assert callable(module.main)
    assert sys.path == original_path
    assert capsys.readouterr() == ("", "")


def test_an_unfragmented_unique_spine_target_is_anchorable(tmp_path):
    module = _module()
    path = _kepub(tmp_path, ["chapter.xhtml"])

    assert module.analyse(path) == (1, 1, 0, 0, [])


def test_a_fragmented_target_is_not_anchorable(tmp_path):
    module = _module()
    path = _kepub(tmp_path, ["chapter.xhtml#chapter-two"])

    assert module.analyse(path) == (1, 0, 1, 0, ["chapter-two"])


def test_two_toc_entries_colliding_on_one_document_reach_only_one_chapter(tmp_path):
    module = _module()
    path = _kepub(tmp_path, ["chapter.xhtml", "chapter.xhtml"])

    assert module.analyse(path) == (2, 1, 0, 1, [])


def test_residue_names_separate_a_licence_footer_from_real_chapters(
    tmp_path, monkeypatch, capsys
):
    module = _module()
    _kepub(
        tmp_path,
        ["chapter.xhtml", "chapter.xhtml#chapter-two", "chapter.xhtml#pg-footer-heading"],
    )
    calls = []

    def normalize(path, *, split_chapters):
        calls.append((Path(path).name, split_chapters))

    monkeypatch.setattr(module, "_normalizer", lambda: normalize)

    assert module.main([str(tmp_path), "--no-split", "--residue"]) == 0

    output = capsys.readouterr().out
    assert "before: 1 of 3    after: 1 of 3" in output
    assert "     2  still carries a #fragment" in output
    assert "     1  #chapter-two" in output
    assert "     1  #pg-footer-heading" in output
    assert "1 of 2 fragmented targets are a licence footer" in output
    assert calls == [("w.kepub", False), ("r.kepub", False)]


def test_default_measurement_keeps_the_splitter_enabled(tmp_path, monkeypatch, capsys):
    module = _module()
    _kepub(tmp_path, ["chapter.xhtml"])
    split_values = []

    def normalize(_path, *, split_chapters):
        split_values.append(split_chapters)

    monkeypatch.setattr(module, "_normalizer", lambda: normalize)

    assert module.main([str(tmp_path)]) == 0
    capsys.readouterr()
    assert split_values == [True]
