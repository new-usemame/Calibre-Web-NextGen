# SPDX-License-Identifier: GPL-3.0-or-later
"""Real process/lock boundaries for the optional managed Calibre server."""
import importlib.util
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
GUARD = Path(__file__).resolve().parents[2] / "cps" / "calibre_server_guard.py"


@pytest.fixture
def ownership():
    spec = importlib.util.spec_from_file_location("guard_under_test", GUARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def eventually(check, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    assert check(), "condition was not observed before the bounded deadline"


def launch(tmp_path):
    receipt = tmp_path / "child.pid"
    program = "import os,time,pathlib;pathlib.Path({!r}).write_text(str(os.getpid()));time.sleep(30)".format(str(receipt))
    supervisor = subprocess.Popen(
        [sys.executable, str(GUARD), str(tmp_path), sys.executable, "-c", program],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        eventually(receipt.exists)
    except BaseException:
        supervisor.stdin.close()
        supervisor.wait(timeout=10)
        raise
    return supervisor, int(receipt.read_text())


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_parent_lifeline_closes_only_after_actual_child_is_reaped(ownership, tmp_path):
    supervisor, child = launch(tmp_path)
    try:
        assert ownership.busy(str(tmp_path), "owner")
        supervisor.stdin.close()
        supervisor.wait(timeout=10)
        assert supervisor.returncode >= 0, supervisor.stderr.read().decode()
        assert not alive(child), "app exit left its Calibre child alive"
        assert not ownership.busy(str(tmp_path), "owner")
    finally:
        if supervisor.poll() is None:
            supervisor.terminate()
            supervisor.wait(timeout=10)


def test_maintenance_waits_for_inflight_operation_then_retains_child_owned_hold(ownership, tmp_path):
    supervisor, child = launch(tmp_path)
    entered = threading.Event()
    finish = threading.Event()
    failures = []

    def converter():
        try:
            with ownership.maintenance(str(tmp_path), timeout=8):
                entered.set()
                finish.wait(timeout=8)
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=converter)
    try:
        with ownership.operation(str(tmp_path)):
            worker.start()
            eventually(lambda: ownership.busy(str(tmp_path), "maintenance"))
            time.sleep(0.3)
            assert alive(child), "maintenance killed an in-flight export"
            assert not entered.is_set(), "maintenance entered before server reap"
        assert entered.wait(timeout=8)
        supervisor.wait(timeout=10)
        assert supervisor.returncode == 75, "maintenance needs a distinct guardian exit reason"
        assert not alive(child)
        assert ownership.busy(str(tmp_path), "maintenance")
        assert not ownership.busy(str(tmp_path), "owner")
        finish.set()
        worker.join(timeout=8)
        assert not worker.is_alive()
        assert not failures
        assert not ownership.busy(str(tmp_path), "maintenance")
    finally:
        finish.set()
        if supervisor.poll() is None:
            supervisor.stdin.close()
            supervisor.wait(timeout=10)
        worker.join(timeout=8)


def test_second_maintenance_cannot_steal_live_owner(ownership, tmp_path):
    with ownership.maintenance(str(tmp_path)):
        with pytest.raises(RuntimeError, match="already running"):
            with ownership.maintenance(str(tmp_path)):
                pytest.fail("second maintenance entered")
        assert ownership.busy(str(tmp_path), "maintenance")
    assert not ownership.busy(str(tmp_path), "maintenance")


def test_operation_respects_the_existing_metadata_lock_directory(ownership, tmp_path, monkeypatch):
    """Manager transitions and old writer callers must contend on the same file."""
    config = tmp_path / "config"
    config.mkdir()
    override = tmp_path / "writer-locks"
    override.mkdir()
    monkeypatch.setenv("CWA_METADATA_LOCK_DIR", str(override))
    with ownership._writes.metadata_db_write_lock():
        with pytest.raises(TimeoutError):
            with ownership.operation(str(config), timeout=0.05):
                pytest.fail("manager bypassed the existing writer lock")
    with ownership.operation(str(config), timeout=0.05):
        pass
    assert not (config / ownership._writes.DEFAULT_LOCK_BASENAME).exists()


def test_parent_stop_interrupts_maintenance_drain_while_parent_holds_operation_gate(ownership, tmp_path):
    """Stopping the guardian must not wait on the gate held by its stopping parent."""
    supervisor, child = launch(tmp_path)
    fd = ownership._open_lock(str(tmp_path), "maintenance")
    try:
        with ownership.operation(str(tmp_path)):
            assert ownership._locks.acquire(fd, blocking=False)
            time.sleep(0.3)  # guardian is attempting its bounded drain acquisition
            supervisor.terminate()
            supervisor.wait(timeout=3)
            assert not alive(child)
            assert not ownership.busy(str(tmp_path), "owner")
    finally:
        ownership._locks.release(fd)
        os.close(fd)
        if supervisor.poll() is None:
            supervisor.stdin.close()
            supervisor.wait(timeout=10)


@pytest.mark.skipif(os.name == "nt", reason="managed content server is POSIX-only")
def test_raw_import_child_retains_maintenance_after_ingest_parent_dies(ownership, tmp_path):
    scripts = GUARD.parents[1] / "scripts"
    receipt = tmp_path / "import-child.pid"
    child_program = "import os,time,pathlib;pathlib.Path({!r}).write_text(str(os.getpid()));time.sleep(30)".format(str(receipt))
    parent_program = """
import subprocess, sys
sys.path.insert(0, {scripts!r})
from calibre_library_target import (offline_library_operation, offline_child_ownership,
                                    operation, offline_writer_ownership)
@offline_library_operation
def import_book():
    with operation() as fd, offline_writer_ownership(fd):
        child = subprocess.Popen([sys.executable, '-c', {child!r}], **offline_child_ownership())
        sys.stdin.read(1)
        child.terminate()
        child.wait(timeout=5)
import_book()
""".format(scripts=str(scripts), child=child_program)
    parent = subprocess.Popen([sys.executable, "-c", parent_program], stdin=subprocess.PIPE,
                              env=dict(os.environ, CALIBRE_DBPATH=str(tmp_path / "app.db")))
    child = None
    try:
        eventually(receipt.exists)
        child = int(receipt.read_text())
        assert ownership.busy(str(tmp_path), "maintenance")
        parent.kill()
        parent.wait(timeout=5)
        assert alive(child)
        assert ownership.busy(str(tmp_path), "maintenance"), "app restart can steal an unfinished raw import"
        with pytest.raises(TimeoutError):
            with ownership.operation(str(tmp_path), timeout=0.05):
                pytest.fail("another metadata writer can enter while the orphaned import is still running")
    finally:
        if parent.poll() is None:
            parent.stdin.close()
            parent.wait(timeout=8)
        if child is not None and alive(child):
            import signal
            os.kill(child, signal.SIGTERM)
        eventually(lambda: not ownership.busy(str(tmp_path), "maintenance"))


def test_conversion_wait_budget_outlasts_a_short_raw_ingest(ownership, tmp_path):
    entered = threading.Event()
    failures = []
    def conversion():
        try:
            with ownership.maintenance(str(tmp_path), timeout=3, wait_timeout=3):
                entered.set()
        except BaseException as error:
            failures.append(error)
    with ownership.maintenance(str(tmp_path)):
        worker = threading.Thread(target=conversion)
        worker.start()
        time.sleep(1.15)
        assert not entered.is_set(), "conversion stole an active raw import"
    worker.join(timeout=4)
    assert not worker.is_alive() and not failures
    assert entered.is_set(), "a short raw ingest incorrectly caused conversion failure"
