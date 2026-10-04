# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Coverage must match real pytest selections and reject partial collection."""

from pathlib import Path
import os
import re
import signal
import subprocess
import sys
import time

import pytest

from tests.ci_lane_collection import collect_ci_coverage
from tests.unit import test_ci_test_lanes as lanes


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    (tmp_path / "tests/unit").mkdir(parents=True)
    (tmp_path / "tests/integration").mkdir()
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n fast\n slow\n device(serial): device identity\n"
    )
    (tmp_path / "tests/unit/test_mixed.py").write_text('''import pytest
pytestmark = pytest.mark.fast
def test_plain(): pass
@pytest.mark.slow
class TestSlow:
    def test_inherited(self): pass
@pytest.mark.device(serial="phone")
@pytest.mark.parametrize("value", [1, 2])
def test_param(value): pass
''')
    (tmp_path / "tests/integration/test_outside.py").write_text(
        "import pytest\n@pytest.mark.fast\ndef test_outside(): pass\n"
    )
    return tmp_path


def test_one_inventory_matches_real_path_and_marker_selections(tree):
    invocations = [
        (["tests/unit"], "fast and not slow"),
        (["tests/unit/test_mixed.py::TestSlow"], "slow"),
        (["tests/unit/test_mixed.py::test_param"], "device(serial='phone')"),
    ]
    result = collect_ci_coverage(tree, invocations)
    for (paths, marker), selected in zip(invocations, result["selected"]):
        actual = subprocess.run(
            [sys.executable, "-m", "pytest", "-o", "addopts=", "-q",
             "--collect-only", *paths, "-m", marker],
            cwd=tree, capture_output=True, text=True, timeout=30,
        )
        assert actual.returncode == 0, actual.stdout + actual.stderr
        nodes = set(re.findall(r"^(\S+::\S+)\s*$", actual.stdout, re.M))
        assert set(selected) == nodes
    covered = set().union(*(set(nodes) for nodes in result["selected"]))
    assert set(result["all"]) - covered == {
        "tests/integration/test_outside.py::test_outside"
    }
    # A covered file must not hide its one uncovered method.
    partial = collect_ci_coverage(tree, [invocations[0]])
    assert "tests/unit/test_mixed.py::TestSlow::test_inherited" in (
        set(partial["all"]) - set(partial["selected"][0])
    )


def test_partial_collection_is_rejected_instead_of_returning_nodeids(tree, monkeypatch):
    (tree / "tests/unit/test_broken.py").write_text("raise RuntimeError('broken import')\n")
    monkeypatch.setattr(lanes, "REPO", tree)
    with pytest.raises(AssertionError, match="CI lane collection failed"):
        lanes._collect_nodeids(["tests"])


@pytest.mark.parametrize("paths,marker", [
    (["tests/missing"], None),
    (["tests/unit/test_mixed.py::no_such_test"], None),
    (["tests/unit"], "fast and ("),
])
def test_invalid_workflow_selection_fails_loudly(tree, paths, marker):
    with pytest.raises(AssertionError, match="CI lane collection failed"):
        collect_ci_coverage(tree, [(paths, marker)])


@pytest.mark.parametrize("resists_term", [False, True])
def test_killed_parent_does_not_abandon_its_collecting_child(tree, resists_term):
    ready, terminated = tree / "ready", tree / "terminated"
    (tree / "tests/unit/test_blocking.py").write_text(
        "import os,signal,time\nfrom pathlib import Path\n"
        "def stopped(signum, frame):\n"
        f" Path({str(terminated)!r}).write_text(str(os.getpid()))\n"
        " os._exit(0)\n"
        + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if resists_term else
         "signal.signal(signal.SIGTERM, stopped)\n")
        + f"Path({str(ready)!r}).write_text(str(os.getpid()))\n"
        "while True: time.sleep(0.1)\n"
    )
    source_root = Path(__file__).resolve().parents[2]
    code = (
        "import os,signal,sys,threading\n"
        "def parent_gone():\n"
        " os.read(0,1)\n os.killpg(os.getpid(),signal.SIGTERM)\n"
        "threading.Thread(target=parent_gone,daemon=True).start()\n"
        f"sys.path.insert(0,{str(source_root)!r})\n"
        "from tests.ci_lane_collection import collect_ci_coverage\n"
        f"collect_ci_coverage({str(tree)!r},[(['tests'],None)])\n"
    )
    parent = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                              start_new_session=True)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            assert parent.poll() is None, "collector parent exited before readiness"
            time.sleep(0.05)
        assert ready.exists(), "blocking collector did not reach its import"
        child = int(ready.read_text())
        parent.terminate()
        assert parent.wait(timeout=10) == -signal.SIGTERM
        if not resists_term:
            deadline = time.monotonic() + 10
            while not terminated.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert terminated.read_text() == str(child), "parent EOF did not terminate collector"
        _assert_process_gone(child)
    finally:
        try:
            parent.stdin.close()
            if parent.poll() is None:
                parent.terminate()
                parent.wait(timeout=10)
        finally:
            if ready.exists():
                _cleanup_fixture_process(int(ready.read_text()))


def _assert_process_gone(pid):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    pytest.fail("owned fixture process still exists: %s" % pid)


def test_completed_collector_does_not_leave_inherited_output_descendant(tree):
    ready = tree / "descendant"
    child = ("import os,signal,time; from pathlib import Path; "
             "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
             f"Path({str(ready)!r}).write_text(str(os.getpid())); "
             "time.sleep(60)")
    (tree / "tests/unit/test_spawn.py").write_text(
        "import subprocess,sys,time\nfrom pathlib import Path\n"
        f"subprocess.Popen([sys.executable,'-c',{child!r}])\n"
        f"ready=Path({str(ready)!r})\n"
        "deadline=time.monotonic()+10\n"
        "while not ready.exists() and time.monotonic()<deadline: time.sleep(0.05)\n"
        "assert ready.exists()\n"
        "def test_collected(): pass\n"
    )
    try:
        result = collect_ci_coverage(tree, [(["tests"], None)], timeout=20)
        assert "tests/unit/test_spawn.py::test_collected" in result["all"]
        _assert_process_gone(int(ready.read_text()))
    finally:
        if ready.exists():
            _cleanup_fixture_process(int(ready.read_text()))


def _cleanup_fixture_process(pid):
    """Failure fallback for the one synthetic PID recorded by this fixture."""
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    _assert_process_gone(pid)
