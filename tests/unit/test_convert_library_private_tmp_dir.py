# -*- coding: utf-8 -*-
# Calibre-Web Automated - fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert Library works in its own temp directory, beside the shared one.

ingest_processor.py rmtree()s the shared tmp_conversion_dir after every run and
Convert Library emptied it after every book, with separate locks, so a run
during an ingest could delete the book the other was converting.
"""

import shutil
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(REPO_ROOT / "scripts")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import convert_library  # noqa: E402


def test_private_tmp_dir_sits_beside_the_shared_one(tmp_path, monkeypatch):
    registered = []
    monkeypatch.setattr(convert_library.atexit, "register", lambda *a: registered.append(a))
    shared = tmp_path / ".cwa_conversion_tmp"
    shared.mkdir()

    private = Path(convert_library.make_private_tmp_dir(str(shared) + "/"))

    assert private.is_dir()
    assert private.parent == tmp_path
    assert private.name.startswith(convert_library.PRIVATE_TMP_PREFIX)
    assert registered and registered[0][1] == str(private), "the dir is removed when the run exits"

    # what the ingest processor does at the end of every run
    (private / "Book.epub").write_text("converting", encoding="utf-8")
    shutil.rmtree(shared, ignore_errors=True)
    assert (private / "Book.epub").exists()


def test_leftovers_from_killed_runs_are_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(convert_library.atexit, "register", lambda *a: None)
    leftover = tmp_path / (convert_library.PRIVATE_TMP_PREFIX + "old")
    leftover.mkdir()
    (leftover / "half.epub").write_text("x", encoding="utf-8")
    shared = tmp_path / ".cwa_conversion_tmp"
    shared.mkdir()
    (shared / "ingest.epub").write_text("x", encoding="utf-8")

    convert_library.make_private_tmp_dir(str(shared) + "/")

    assert not leftover.exists()
    assert (shared / "ingest.epub").exists(), "the shared dir belongs to ingest"


def test_works_when_ingest_has_already_removed_the_shared_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(convert_library.atexit, "register", lambda *a: None)
    shared = tmp_path / "config" / ".cwa_conversion_tmp"

    private = Path(convert_library.make_private_tmp_dir(str(shared) + "/"))

    assert private.is_dir()
    assert private.parent == shared.parent
