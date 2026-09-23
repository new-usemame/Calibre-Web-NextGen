# -*- coding: utf-8 -*-
# Calibre-Web Automated - fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert Library must read the book list even when Calibre adds a warning (#1954).

Calibre prints some diagnostics to stdout with a bare print(). When its config
directory is not writable it appends "No write access to <dir> using a temporary
dir instead" to the same stream that carries `calibredb list --for-machine`
JSON. Convert Library parsed the whole stream as one JSON document, failed on
the trailing text, and reported "No books found in library" for a 1,200-book
library. The fake calibredb below replays the reporter's output shape through
the real subprocess call.
"""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(REPO_ROOT / "scripts")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import convert_library  # noqa: E402

BOOKS = [
    {"formats": ["/calibre-library/Martha Wells/Queen Demon (7)/Queen Demon - Martha Wells.epub"], "id": 7},
    {"formats": ["/calibre-library/Sue Burke/Semiosis (1228)/Semiosis - Sue Burke.azw3"], "id": 1228},
]
WARNING = "No write access to /root/.config/calibre using a temporary dir instead"


def _converter_with_fake_calibredb(tmp_path, stdout_text):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    payload = tmp_path / "calibredb-stdout.txt"
    payload.write_text(stdout_text, encoding="utf-8")
    fake = bin_dir / "calibredb"
    fake.write_text('#!/bin/sh\nexec cat "%s"\n' % payload, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

    converter = convert_library.LibraryConverter.__new__(convert_library.LibraryConverter)
    converter.verbose = False
    converter.library_dir = str(tmp_path / "library") + "/"
    converter.calibre_env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return converter


@pytest.fixture
def log_lines(monkeypatch):
    lines = []
    monkeypatch.setattr(convert_library, "print_and_log", lambda message, *a, **k: lines.append(str(message)))
    return lines


def test_warning_after_the_json_does_not_hide_the_library(tmp_path, log_lines):
    # The reporter's exact shape: JSON with no trailing newline, then the
    # warning twice.
    stdout_text = json.dumps(BOOKS, indent=2) + WARNING + "\n" + WARNING + "\n"
    converter = _converter_with_fake_calibredb(tmp_path, stdout_text)

    formats = converter.get_library_book_formats()

    assert formats == {7: BOOKS[0]["formats"], 1228: BOOKS[1]["formats"]}
    assert any(WARNING in line for line in log_lines), (
        "Calibre's own warning should still reach the log; it explains a "
        "misconfigured config directory")


def test_warning_before_the_json_does_not_hide_the_library(tmp_path, log_lines):
    stdout_text = WARNING + "\n" + json.dumps(BOOKS, indent=2) + "\n"
    converter = _converter_with_fake_calibredb(tmp_path, stdout_text)

    assert converter.get_library_book_formats() == {
        7: BOOKS[0]["formats"], 1228: BOOKS[1]["formats"]}


def test_a_bracketed_diagnostic_before_the_json_is_not_taken_for_the_list(tmp_path, log_lines):
    stdout_text = "[1] " + WARNING + "\n" + json.dumps(BOOKS, indent=2) + "\n"
    converter = _converter_with_fake_calibredb(tmp_path, stdout_text)

    assert converter.get_library_book_formats() == {
        7: BOOKS[0]["formats"], 1228: BOOKS[1]["formats"]}


def test_output_with_no_book_list_is_still_reported_unparseable(tmp_path, log_lines):
    converter = _converter_with_fake_calibredb(tmp_path, WARNING + "\n")

    assert converter.get_library_book_formats() == {}
    assert any("Failed to parse calibredb command output" in line for line in log_lines)
