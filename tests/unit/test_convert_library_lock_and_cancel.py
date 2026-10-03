# -*- coding: utf-8 -*-
# Calibre-Web Automated - fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert Library's single-instance lock and the web UI's Cancel.

A run killed with SIGKILL or by the OOM killer never reaches atexit, so its
lock stayed and every later run exited with "already running" until the
container restarted. The web UI's Cancel sends SIGTERM, whose default action
also skips atexit and leaves the running ebook-convert going.

The lock owner and the running tool are real processes here.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = str(REPO_ROOT / "scripts")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import convert_library  # noqa: E402


@pytest.fixture
def log_lines(monkeypatch):
    lines = []
    monkeypatch.setattr(convert_library, "print_and_log", lambda message, *a, **k: lines.append(str(message)))
    return lines


def _sleeper(*extra):
    """A real process; extra args land in its /proc cmdline."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", *extra])


def test_lock_is_taken_and_records_our_pid(tmp_path):
    lock = tmp_path / "convert_library.lock"
    assert convert_library.acquire_lock(str(lock))
    assert lock.read_text() == str(os.getpid())


def test_lock_held_by_a_running_convert_library_is_respected(tmp_path):
    lock = tmp_path / "convert_library.lock"
    owner = _sleeper("convert_library")
    try:
        lock.write_text(str(owner.pid))
        assert not convert_library.acquire_lock(str(lock))
        assert lock.read_text() == str(owner.pid)
    finally:
        owner.kill()
        owner.wait()


@pytest.mark.parametrize("content", ["", "not-a-pid", "dead", "reused"])
def test_stale_lock_is_cleared(tmp_path, log_lines, content):
    lock = tmp_path / "convert_library.lock"
    other = None
    if content == "dead":
        gone = _sleeper()
        gone.kill()
        gone.wait()
        content = str(gone.pid)
    elif content == "reused":
        # a live process that isn't convert_library holds the recorded PID
        other = _sleeper()
        content = str(other.pid)
    try:
        lock.write_text(content)
        assert convert_library.acquire_lock(str(lock))
        assert lock.read_text() == str(os.getpid())
        assert any("stale lock" in line for line in log_lines)
    finally:
        if other:
            other.kill()
            other.wait()


def test_sigterm_stops_the_running_tool_and_exits_through_atexit(monkeypatch, log_lines):
    child = _sleeper()
    monkeypatch.setattr(convert_library, "_current_child", child)
    try:
        with pytest.raises(SystemExit) as exit_info:
            convert_library._stop_on_sigterm(15, None)
        assert exit_info.value.code == 143
        assert child.poll() is not None, "the running tool must not outlive a cancel"
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


def test_sigterm_with_nothing_running_still_exits(monkeypatch, log_lines):
    monkeypatch.setattr(convert_library, "_current_child", None)
    with pytest.raises(SystemExit):
        convert_library._stop_on_sigterm(15, None)


def test_run_streaming_tracks_and_then_clears_the_current_child(tmp_path, monkeypatch):
    converter = convert_library.LibraryConverter.__new__(convert_library.LibraryConverter)
    converter.verbose = False
    seen = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: seen.append(convert_library._current_child))

    converter._run_streaming([sys.executable, "-c", "print('ok')"])

    assert seen and seen[0] is not None, "the running tool must be visible to the SIGTERM handler"
    assert convert_library._current_child is None


def test_running_the_script_clears_a_stale_lock_and_cancel_runs_atexit(tmp_path):
    """End to end through the __main__ path: a lock left by a killed run does
    not block the next run, and SIGTERM still removes the lock on the way out."""
    lock = tmp_path / "convert_library.lock"
    lock.write_text("999999999")
    code = (
        "import sys, os, signal, time\n"
        f"sys.path.insert(0, {SCRIPTS_DIR!r})\n"
        "import convert_library as cl\n"
        f"cl.LOCK_PATH = {str(lock)!r}\n"
        "cl._acquire_lock_or_exit()\n"
        "signal.signal(signal.SIGTERM, cl._stop_on_sigterm)\n"
        "print('ready', flush=True)\n"
        "time.sleep(30)\n"
    )
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(REPO_ROOT), SCRIPTS_DIR]))
    proc = subprocess.Popen([sys.executable, "-c", code, "convert_library"],
                            stdout=subprocess.PIPE, text=True, env=env)
    try:
        for line in proc.stdout:  # importing the module logs a few lines first
            if line.strip() == "ready":
                break
        assert lock.read_text() == str(proc.pid)
        proc.terminate()
        assert proc.wait(timeout=15) == 143
        assert not lock.exists(), "atexit must remove the lock after a cancel"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_remove_lock_only_removes_our_own_lock(tmp_path):
    """Cancel deletes the lock right after SIGTERM; a new run may take it before this one exits."""
    lock = tmp_path / "convert_library.lock"
    lock.write_text(str(os.getpid()))
    convert_library.removeLock(str(lock))
    assert not lock.exists()

    lock.write_text("999999999")  # another run's lock
    convert_library.removeLock(str(lock))
    assert lock.read_text() == "999999999"
