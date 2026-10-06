# -*- coding: utf-8 -*-
# Calibre-Web Automated - fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert Library works in its own subdirectory of the shared temp dir, and ingest leaves it alone.

ingest_processor.py removed the whole shared tmp_conversion_dir after every book and
Convert Library emptied it after every book, under separate locks, so a run during an
ingest could delete the book the other was converting (#2425, @splitsec2).
"""

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(REPO_ROOT / "scripts")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import convert_library  # noqa: E402
import ingest_processor  # noqa: E402


@pytest.fixture(autouse=True)
def _no_atexit(monkeypatch):
    registered = []
    monkeypatch.setattr(convert_library.atexit, "register", lambda *a: registered.append(a))
    return registered


def test_ingest_cleanup_leaves_a_convert_library_run_alone(tmp_path, _no_atexit):
    shared = tmp_path / ".cwa_conversion_tmp"
    shared.mkdir()

    private = Path(convert_library.make_private_tmp_dir(str(shared) + "/"))
    (private / "Book.mobi").write_text("converting", encoding="utf-8")
    (shared / "ingest-output.epub").write_text("x", encoding="utf-8")
    (shared / "staging").mkdir()
    (shared / "staging" / "upload.epub").write_text("x", encoding="utf-8")

    # what the ingest processor does at the end of every book
    ingest_processor.empty_tmp_conversion_dir(str(shared) + "/")

    assert (private / "Book.mobi").exists(), "ingest deleted the book Convert Library was converting"
    assert sorted(p.name for p in shared.iterdir()) == [private.name], "ingest must still clear its own files"
    assert private.parent == shared, "scratch files must stay on the configured CWA_TMP_CONVERSION_DIR volume"
    assert private.name.startswith(f"{convert_library.PRIVATE_TMP_PREFIX}{os.getpid()}_")
    assert private.name.startswith(ingest_processor.CONVERT_LIBRARY_TMP_PREFIX)
    assert (convert_library.shutil.rmtree, str(private), True) in _no_atexit, "the dir is removed when the run exits"


def test_ingest_cleanup_of_a_missing_dir_is_a_no_op(tmp_path):
    ingest_processor.empty_tmp_conversion_dir(str(tmp_path / "gone") + "/")


def test_leftovers_from_killed_runs_are_removed(tmp_path):
    shared = tmp_path / ".cwa_conversion_tmp"
    leftover = shared / (convert_library.PRIVATE_TMP_PREFIX + "123_old")
    leftover.mkdir(parents=True)
    (leftover / "half.epub").write_text("x", encoding="utf-8")
    (shared / "ingest.epub").write_text("x", encoding="utf-8")

    convert_library.make_private_tmp_dir(str(shared) + "/")

    assert not leftover.exists()
    assert (shared / "ingest.epub").exists(), "the rest of the shared dir belongs to ingest"


def test_works_when_the_shared_dir_does_not_exist_yet(tmp_path):
    shared = tmp_path / "config" / ".cwa_conversion_tmp"

    private = Path(convert_library.make_private_tmp_dir(str(shared) + "/"))

    assert private.is_dir()
    assert private.parent == shared


def test_a_run_with_nowhere_to_work_ends_instead_of_hanging(tmp_path, monkeypatch, caplog):
    """The web status page waits for "Run Ended"; a crash before it left the page polling."""
    monkeypatch.setattr(convert_library, "print_and_log", lambda *a, **k: None)

    def mkdtemp(prefix=None, dir=None):
        raise OSError(30, "Read-only file system", dir)

    monkeypatch.setattr(convert_library.tempfile, "mkdtemp", mkdtemp)
    with caplog.at_level("INFO", logger=convert_library.logger.name), pytest.raises(SystemExit) as exited:
        convert_library.make_private_tmp_dir(str(tmp_path / "shared") + "/")
    assert exited.value.code == 2
    assert "Run Ended" in caplog.text
