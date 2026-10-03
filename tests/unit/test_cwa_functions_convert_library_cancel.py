# -*- coding: utf-8 -*-
# Calibre-Web Automated - fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cancelling Convert Library must clean up its own files, not ingest's.

The cancel path used to empty the shared tmp_conversion_dir, which an ingest
may be converting a book in at the same moment.
"""

import queue
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(REPO_ROOT / "scripts")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


@pytest.fixture
def cwa_functions():
    from cps import cwa_functions
    return cwa_functions


def _dirs(tmp_path):
    shared = tmp_path / ".cwa_conversion_tmp"
    shared.mkdir()
    (shared / "ingest.epub").write_text("x", encoding="utf-8")
    mine = tmp_path / ".cwa_convert_library_abc123"
    mine.mkdir()
    (mine / "half.epub").write_text("x", encoding="utf-8")
    return shared, mine


def test_cancel_cleanup_removes_only_convert_library_dirs(cwa_functions, tmp_path):
    shared, mine = _dirs(tmp_path)

    cwa_functions.remove_convert_library_tmp_dirs(str(shared) + "/")

    assert not mine.exists()
    assert (shared / "ingest.epub").exists()


def test_cleanup_finds_the_dir_convert_library_makes(cwa_functions, tmp_path, monkeypatch):
    """The two modules share a prefix; this fails if they drift apart."""
    import convert_library
    monkeypatch.setattr(convert_library.atexit, "register", lambda *a: None)
    shared = tmp_path / ".cwa_conversion_tmp"
    shared.mkdir()
    private = Path(convert_library.make_private_tmp_dir(str(shared) + "/"))

    cwa_functions.remove_convert_library_tmp_dirs(str(shared) + "/")

    assert not private.exists()
    assert shared.is_dir()


def test_cancel_leaves_an_ingest_in_the_shared_dir_alone(cwa_functions, tmp_path, monkeypatch):
    """Drive the real cancel loop: trigger file present, process in the queue."""
    shared, mine = _dirs(tmp_path)
    log = tmp_path / "convert-library.log"
    log.write_text("", encoding="utf-8")
    (tmp_path / ".kill_convert_library_trigger").write_text("", encoding="utf-8")
    monkeypatch.setattr(cwa_functions.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(cwa_functions, "_service_log_path", lambda name: str(log))
    monkeypatch.setattr(cwa_functions, "get_tmp_conversion_dir", lambda: str(shared) + "/")
    monkeypatch.setattr(cwa_functions, "archive_run_log", lambda path: None)

    class _Process:
        terminated = False

        def terminate(self):
            self.terminated = True

    process = _Process()
    q = queue.Queue()
    q.put(process)

    cwa_functions.kill_convert_library(q)

    assert process.terminated
    assert not mine.exists(), "the cancelled run's half finished files are removed"
    assert (shared / "ingest.epub").exists(), "an ingest in progress keeps its book"
