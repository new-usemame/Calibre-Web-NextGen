# -*- coding: utf-8 -*-
# Calibre-Web Automated - fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cancelling Convert Library must clean up its own files, not ingest's or a newer run's.

The cancel path used to empty the shared tmp_conversion_dir, which an ingest
may be converting a book in at the same moment.
"""

import queue
import subprocess
import sys
import textwrap
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
    mine = tmp_path / ".cwa_convert_library_4242_abc123"
    mine.mkdir()
    (mine / "half.epub").write_text("x", encoding="utf-8")
    return shared, mine


def test_cancel_cleanup_removes_only_that_runs_dirs(cwa_functions, tmp_path):
    shared, mine = _dirs(tmp_path)
    newer = tmp_path / ".cwa_convert_library_5151_def456"
    newer.mkdir()

    cwa_functions.remove_convert_library_tmp_dirs(str(shared) + "/", 4242)

    assert not mine.exists()
    assert newer.exists(), "a run that started since keeps its dir"
    assert (shared / "ingest.epub").exists()


def test_cancel_cleanup_also_checks_the_system_temp_dir(cwa_functions, tmp_path, monkeypatch):
    system_tmp = tmp_path / "systmp"
    system_tmp.mkdir()
    monkeypatch.setattr(cwa_functions.tempfile, "gettempdir", lambda: str(system_tmp))
    fallback = system_tmp / ".cwa_convert_library_4242_abc123"
    fallback.mkdir()
    cwa_functions.remove_convert_library_tmp_dirs(str(tmp_path / "shared") + "/", 4242)
    assert not fallback.exists()


def test_cleanup_finds_the_dir_convert_library_makes(cwa_functions, tmp_path, monkeypatch):
    """The two modules share a prefix; this fails if they drift apart."""
    import convert_library
    monkeypatch.setattr(convert_library.atexit, "register", lambda *a: None)
    shared = tmp_path / ".cwa_conversion_tmp"
    shared.mkdir()
    private = Path(convert_library.make_private_tmp_dir(str(shared) + "/"))

    cwa_functions.remove_convert_library_tmp_dirs(str(shared) + "/", __import__("os").getpid())

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
        pid = 4242

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    process = _Process()
    q = queue.Queue()
    q.put(process)

    cwa_functions.kill_convert_library(q)

    assert process.terminated
    assert not mine.exists(), "the cancelled run's half finished files are removed"
    assert (shared / "ingest.epub").exists(), "an ingest in progress keeps its book"


SLOW_STOPPER = textwrap.dedent("""
    # A run that takes a moment to stop after SIGTERM
    import signal, sys, time
    def stop(signum, frame):
        time.sleep(1.5)
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    print("ready", flush=True)
    time.sleep(60)
""")


def test_cancel_waits_for_the_run_to_stop_before_cleaning_up(cwa_functions, tmp_path, monkeypatch):
    system_tmp = tmp_path / "systmp"
    system_tmp.mkdir()
    monkeypatch.setattr(cwa_functions.tempfile, "gettempdir", lambda: str(system_tmp))
    monkeypatch.setattr(cwa_functions, "get_tmp_conversion_dir", lambda: str(tmp_path / "shared") + "/")

    proc = subprocess.Popen([sys.executable, "-c", SLOW_STOPPER], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "ready"
        lock = system_tmp / "convert_library.lock"
        lock.write_text(str(proc.pid), encoding="utf-8")
        stopping = tmp_path / f".cwa_convert_library_{proc.pid}_x"
        stopping.mkdir()
        # The lock and folder must still be there while the run is stopping, which is
        # what keeps a new run from starting and deleting that folder.
        seen = []
        real_remove = cwa_functions.remove_convert_library_tmp_dirs

        def record(*args):
            seen.append(proc.poll())
            real_remove(*args)

        monkeypatch.setattr(cwa_functions, "remove_convert_library_tmp_dirs", record)
        cwa_functions.stop_convert_library(proc)
        assert seen and seen[0] is not None, "cleanup ran after the process had exited"
        assert not lock.exists()
        assert not stopping.exists()
    finally:
        proc.kill()
        proc.wait()


def test_cancel_leaves_a_lock_a_newer_run_has_taken(cwa_functions, tmp_path, monkeypatch):
    system_tmp = tmp_path / "systmp"
    system_tmp.mkdir()
    monkeypatch.setattr(cwa_functions.tempfile, "gettempdir", lambda: str(system_tmp))
    monkeypatch.setattr(cwa_functions, "get_tmp_conversion_dir", lambda: str(tmp_path / "shared") + "/")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        lock = system_tmp / "convert_library.lock"
        lock.write_text("999999999", encoding="utf-8")
        cwa_functions.stop_convert_library(proc)
        assert lock.exists()
    finally:
        proc.kill()
        proc.wait()


def test_cancel_kills_a_run_that_does_not_stop_in_time(cwa_functions, tmp_path, monkeypatch):
    monkeypatch.setattr(cwa_functions.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(cwa_functions, "get_tmp_conversion_dir", lambda: str(tmp_path / "shared") + "/")
    code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(60)"
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "ready"
        cwa_functions.stop_convert_library(proc, wait_seconds=0.5)
        assert proc.poll() is not None
    finally:
        proc.kill()
        proc.wait()
