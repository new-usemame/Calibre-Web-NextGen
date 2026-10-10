# -*- coding: utf-8 -*-
# Calibre-Web Automated - fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert Library with kepub as the target format.

convert_library() passes Path(file).suffix (".epub", with the dot) and
convert_to_kepub() compared it to "epub", so an epub source never took the
direct path and always went through a redundant ebook-convert epub -> epub.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(REPO_ROOT / "scripts")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import convert_library  # noqa: E402


def _write_tool(bin_dir, name, body):
    tool = bin_dir / name
    tool.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    tool.chmod(0o755)


@pytest.fixture
def log_lines(monkeypatch):
    lines = []
    monkeypatch.setattr(convert_library, "print_and_log", lambda message, *a, **k: lines.append(str(message)))
    return lines


@pytest.fixture
def kepub_converter(tmp_path, monkeypatch):
    """A kepub-target converter with fake ebook-convert and kepubify that
    record each call. kepubify runs with the process PATH, not calibre_env."""
    converter = convert_library.LibraryConverter.__new__(convert_library.LibraryConverter)
    converter.verbose = False
    converter.current_book = 1
    converter.to_convert = ["x"]
    converter.tmp_conversion_dir = str(tmp_path / "cwa_conversion_tmp") + "/"
    converter.ensure_tmp_conversion_dir()
    converter.target_format = "kepub"
    converter.cwa_settings = {"auto_backup_conversions": False, "auto_backup_imports": False}
    converter.db = SimpleNamespace(conversion_add_entry=lambda *a: None)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    _write_tool(bin_dir, "ebook-convert", f'echo ebook-convert >> "{calls}"\n'
                'case "$1" in *Locked*) echo "Locked.mobi is DRM locked"; exit 1;; esac\n'
                'echo converted > "$2"\n')
    # Called as: kepubify --inplace --calibre --output <dir> <epub>; write where real kepubify would.
    _write_tool(bin_dir, "kepubify", f'echo kepubify >> "{calls}"\n'
                'echo kepub > "$4/$(basename "$5" .epub).kepub"\n')
    converter.calibre_env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return converter, calls


def test_epub_source_goes_straight_to_kepubify(kepub_converter, tmp_path, log_lines):
    converter, calls = kepub_converter
    book = tmp_path / "Book.epub"
    book.write_text("x", encoding="utf-8")

    ok, target = converter.convert_to_kepub(str(book), book.suffix)

    assert ok
    assert target.endswith("Book.kepub")
    assert os.path.isfile(target) and target.startswith(converter.tmp_conversion_dir)
    assert book.read_text(encoding="utf-8") == "x", "the library's own epub must be left untouched"
    assert calls.read_text().split() == ["kepubify"], "an epub must not be run through ebook-convert first"
    assert any("already in epub format" in line for line in log_lines)


def test_other_formats_still_convert_to_epub_first(kepub_converter, tmp_path, log_lines):
    converter, calls = kepub_converter
    book = tmp_path / "Book.mobi"
    book.write_text("x", encoding="utf-8")

    ok, _ = converter.convert_to_kepub(str(book), book.suffix)

    assert ok
    assert calls.read_text().split() == ["ebook-convert", "kepubify"]


def test_failed_intermediate_conversion_logs_the_tools_reason(kepub_converter, tmp_path, log_lines):
    converter, calls = kepub_converter
    book = tmp_path / "Locked.mobi"
    book.write_text("x", encoding="utf-8")

    ok, _ = converter.convert_to_kepub(str(book), book.suffix)

    assert not ok
    text = "\n".join(log_lines)
    assert "Intermediate conversion of Locked.mobi to epub was unsuccessful" in text
    assert "DRM locked" in text, "the tool's own reason must reach the log file"
    assert "kepubify" not in calls.read_text()
