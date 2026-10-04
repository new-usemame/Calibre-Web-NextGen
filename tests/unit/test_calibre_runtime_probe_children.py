# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioural controls for private Calibre-probe helper lifetime."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

spec = importlib.util.spec_from_file_location(
    "probe_children", Path(__file__).parents[1] / "integration/calibre_runtime_probe_children.py"
)
children = importlib.util.module_from_spec(spec)
spec.loader.exec_module(children)


def completed_helper_case(reap, tmp_path):
    output = tmp_path / "private-config" / "helper-completed"
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
         "print('ready',flush=True); sys.stdin.read(1); "
         "p.parent.mkdir(); p.write_text('complete'); "
         "print('written',flush=True); sys.stdin.read()", str(output)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        child.stdin.write("g")
        child.stdin.flush()
        assert child.stdout.readline().strip() == "written"
        child.stdin.close()
        reap([child.pid], timeout=2)
        assert output.read_text() == "complete"
        # Successful filesystem cleanup starts only after the owned helper is
        # reaped. A no-drain adapter fails this actual waitpid observation.
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()
        child.stdout.close()


def test_completed_helper_is_reaped_before_private_directory_cleanup(tmp_path):
    completed_helper_case(children.reap_owned_children, tmp_path)


def test_original_no_drain_control_fails_the_same_lifetime_oracle(tmp_path):
    with pytest.raises(pytest.fail.Exception):
        completed_helper_case(lambda *_args, **_kwargs: None, tmp_path)


def test_timeout_stays_failed_and_reaps_only_the_owned_helper():
    owned = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with pytest.raises(TimeoutError, match="did not finish"):
            children.reap_owned_children([owned.pid], timeout=.05)
        with pytest.raises(ChildProcessError):
            os.waitpid(owned.pid, os.WNOHANG)
        assert unrelated.poll() is None
    finally:
        for child in (owned, unrelated):
            if child.poll() is None:
                child.kill()
            child.wait()


def test_nonzero_helper_stays_failed_after_reap():
    child = subprocess.Popen([sys.executable, "-c", "raise SystemExit(17)"])
    try:
        with pytest.raises(RuntimeError, match="helper failed"):
            children.reap_owned_children([child.pid], timeout=2)
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
    finally:
        child.wait()


def late_owned_helper_case(drain, failure_mode):
    command = "raise SystemExit(17)" if failure_mode == "nonzero" else "import time; time.sleep(60)"
    failed = subprocess.Popen([sys.executable, "-c", command])
    late = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    first_snapshot = True

    def adopted_inventory():
        # The first snapshot sees the parent; the next sees the newly adopted
        # helper. Both are real waitpid-owned children, not mocked processes.
        nonlocal first_snapshot
        if first_snapshot:
            first_snapshot = False
            return [failed.pid]
        try:
            os.kill(late.pid, 0)
        except ProcessLookupError:
            return []
        return [late.pid]

    try:
        error, message = (RuntimeError, "helper failed") if failure_mode == "nonzero" else (TimeoutError, "did not finish")
        with pytest.raises(error, match=message):
            drain(adopted_inventory, timeout=.05 if failure_mode == "timeout" else 2)
        with pytest.raises(ChildProcessError):
            os.waitpid(late.pid, os.WNOHANG)
    finally:
        for child in (failed, late):
            if child.poll() is None:
                child.kill()
            child.wait()


@pytest.mark.parametrize("failure_mode", ["nonzero", "timeout"])
def test_failed_parent_keeps_failure_and_reaps_late_owned_helper(failure_mode):
    late_owned_helper_case(children.drain_owned_children, failure_mode)


@pytest.mark.parametrize("failure_mode", ["nonzero", "timeout"])
def test_original_exceptional_exit_control_leaves_late_helper_unreaped(failure_mode):
    def old_drain(provider, timeout):
        return children.reap_owned_children(provider(), timeout)
    with pytest.raises(pytest.fail.Exception):
        late_owned_helper_case(old_drain, failure_mode)
