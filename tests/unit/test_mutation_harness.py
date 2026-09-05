# SPDX-License-Identifier: GPL-3.0-or-later
"""The mutation harness must never turn a non-result into a verdict.

Every failure mode this guards was observed by hand in one session: a stale
anchor leaving the mutant unapplied while pytest printed green, two modules
with the same basename overwriting each other's backup, and a restore that was
assumed rather than checked.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.unit

_HARNESS = pathlib.Path(__file__).resolve().parents[1] / "mutation" / "mutate.py"
_spec = importlib.util.spec_from_file_location("mutate", _HARNESS)
mutate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mutate)


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _committed_repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Mutation Tests")
    _git(repo, "config", "user.email", "mutation-tests.invalid")
    (repo / ".gitignore").write_text("*.ignored\n*.pyc\n__pycache__/\n")
    (repo / "victim.py").write_text("VALUE = 1\n")
    (repo / "collateral.py").write_text("ORIGINAL\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "seed")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_isolated_sweep_scrubs_the_full_tree_to_an_immutable_seed(tmp_path):
    repo, seed = _committed_repo(tmp_path)
    state = tmp_path / "state"
    live_before = (repo / "victim.py").read_bytes()

    with mutate.IsolatedSweep.create(repo, seed, state_root=state) as sweep:
        assert sweep.root != repo
        assert _git(sweep.root, "rev-parse", "HEAD") == seed
        (sweep.root / "victim.py").write_text("VALUE = 2\n")
        (sweep.root / "collateral.py").write_text("DAMAGED\n")
        (sweep.root / "run.ignored").write_text("artifact\n")
        (sweep.root / "__pycache__").mkdir()
        (sweep.root / "__pycache__" / "victim.pyc").write_bytes(b"bytecode")
        _git(sweep.root, "add", "victim.py")
        _git(sweep.root, "commit", "-qm", "phase changed HEAD")

        witness = sweep.scrub()

        assert witness == {
            "seed_sha": seed,
            "seed_tree": _git(repo, "rev-parse", f"{seed}^{{tree}}"),
            "head": seed,
            "status_empty": True,
        }
        assert (sweep.root / "victim.py").read_text() == "VALUE = 1\n"
        assert (sweep.root / "collateral.py").read_text() == "ORIGINAL\n"
        assert not (sweep.root / "run.ignored").exists()
        assert not (sweep.root / "__pycache__").exists()
        disposable_root = sweep.root

    assert not disposable_root.exists()
    assert (repo / "victim.py").read_bytes() == live_before
    assert _git(repo, "status", "--porcelain") == ""


def test_startup_reaps_a_dead_tool_owned_sweep(tmp_path):
    repo, seed = _committed_repo(tmp_path)
    state = tmp_path / "state"
    abandoned = mutate.IsolatedSweep.create(repo, seed, state_root=state)
    metadata_path = abandoned.entry / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["owner_pid"] = 2_000_000_000
    metadata_path.write_text(json.dumps(metadata))
    abandoned._closed = True

    replacement = mutate.IsolatedSweep.create(repo, seed, state_root=state)
    try:
        assert not abandoned.root.exists()
        assert replacement.root.exists()
    finally:
        replacement.close()


def test_startup_does_not_reap_a_live_sweep(tmp_path):
    repo, seed = _committed_repo(tmp_path)
    state = tmp_path / "state"
    first = mutate.IsolatedSweep.create(repo, seed, state_root=state)
    second = mutate.IsolatedSweep.create(repo, seed, state_root=state)
    try:
        assert first.root.exists()
        assert second.root.exists()
        metadata = json.loads((first.entry / "metadata.json").read_text())
        assert metadata["owner_pid"] == os.getpid()
    finally:
        second.close()
        first.close()


def _child_program(*, detach=False, timeout=False):
    child = "\n".join([
        "import os,pathlib,signal,time",
        "os.setsid()" if detach else "pass",
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
        "pathlib.Path('ready').write_text(str(os.getpid()))",
        "time.sleep(3)",
        "pathlib.Path('late-write').write_text('contaminated')",
    ])
    return "\n".join([
        "import pathlib,subprocess,sys,time",
        f"child = subprocess.Popen([sys.executable, '-c', {child!r}])",
        "deadline = time.monotonic() + 3",
        "while not pathlib.Path('ready').exists():",
        "    if child.poll() is not None or time.monotonic() > deadline: raise RuntimeError('child did not become ready')",
        "    time.sleep(.005)",
        "print('child ready', flush=True)",
        "time.sleep(30)" if timeout else "pass",
    ])


def _assert_child_gone(root):
    pid = int((root / "ready").read_text())
    # kill(0) also sees zombies: exit is not enough; the OS must have reaped it.
    def exists():
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
    deadline = time.monotonic() + 3
    while exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert not exists(), "phase descendant still exists (including zombie)"
    time.sleep(3.1)
    assert not (root / "late-write").exists(), "child invalidated the boundary after exit"


def test_phase_exit_kills_a_child_that_would_write_later(tmp_path):
    result = mutate.run_phase_process(
        ownership_contract="inherited-token",
        argv=[sys.executable, "-c", _child_program()], cwd=tmp_path,
        environment=os.environ.copy(), timeout=5, artifacts=tmp_path / "artifacts",
    )
    assert "child ready" in result.stdout
    assert result.returncode == 0
    assert result.containment_error is None
    _assert_child_gone(tmp_path)


def test_phase_escape_is_terminated_and_is_an_error(tmp_path):
    result = mutate.run_phase_process(
        ownership_contract="inherited-token",
        argv=[sys.executable, "-c", _child_program(detach=True)], cwd=tmp_path,
        environment=os.environ.copy(), timeout=5, artifacts=tmp_path / "artifacts",
    )
    assert "child ready" in result.stdout
    assert result.returncode == 0
    assert int((tmp_path / "ready").read_text()) in result.escaped_pids
    assert "escaped its process group" in result.containment_error
    _assert_child_gone(tmp_path)


def test_phase_timeout_is_not_a_process_leak(tmp_path):
    result = mutate.run_phase_process(
        ownership_contract="inherited-token",
        argv=[sys.executable, "-c", _child_program(timeout=True)], cwd=tmp_path,
        environment=os.environ.copy(), timeout=1, artifacts=tmp_path / "artifacts",
    )
    assert "child ready" in result.stdout
    assert result.timed_out
    assert result.returncode is not None
    assert result.containment_error is None
    _assert_child_gone(tmp_path)



@pytest.fixture(autouse=True)
def _cleanup_fault_for_red_run(monkeypatch):
    """Test-only negative control; always clean up after the expected failure."""
    if os.environ.get("CWNG_PHASE_CLEANUP") != "disabled":
        yield
        return
    original = mutate._terminate_phase_processes
    pending = []
    def disabled(proc, token):
        pending.append((proc, token))
        return (), None
    monkeypatch.setattr(mutate, "_terminate_phase_processes", disabled)
    try:
        yield
    finally:
        for proc, token in pending:
            original(proc, token)


def test_strict_containment_is_rejected_before_launch(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("an unsupported containment contract launched a process")
    monkeypatch.setattr(mutate.subprocess, "Popen", forbidden)
    with pytest.raises(mutate.IsolationError, match="arbitrary descendant containment"):
        mutate.run_phase_process(
            [sys.executable, "-c", "pass"], cwd=tmp_path,
            environment=os.environ.copy(), timeout=5, artifacts=tmp_path / "artifacts",
        )
    assert not (tmp_path / "artifacts").exists()


def test_process_inspection_failure_is_an_error_and_still_kills_group(tmp_path, monkeypatch):
    def broken(*args):
        raise mutate.IsolationError("injected process inspection failure")
    monkeypatch.setattr(mutate, "_phase_members", broken)
    result = mutate.run_phase_process(
        [sys.executable, "-c", _child_program()], ownership_contract="inherited-token",
        cwd=tmp_path, environment=os.environ.copy(), timeout=5, artifacts=tmp_path / "artifacts",
    )
    assert "injected process inspection failure" in result.containment_error
    _assert_child_gone(tmp_path)


def test_interruption_still_terminates_phase_group(tmp_path, monkeypatch):
    original = mutate.subprocess.Popen
    class InterruptingPopen(original):
        def __init__(self, *args, **kwargs):
            self.inject = kwargs.get("start_new_session", False)
            super().__init__(*args, **kwargs)
        def wait(self, timeout=None):
            if self.inject:
                self.inject = False
                deadline = time.monotonic() + 3
                while not (tmp_path / "ready").exists():
                    assert time.monotonic() < deadline, "child failed to become ready"
                    time.sleep(.005)
                raise KeyboardInterrupt
            return super().wait(timeout=timeout)
    monkeypatch.setattr(mutate.subprocess, "Popen", InterruptingPopen)
    with pytest.raises(KeyboardInterrupt):
        mutate.run_phase_process(
            [sys.executable, "-c", _child_program(timeout=True)],
            ownership_contract="inherited-token", cwd=tmp_path,
            environment=os.environ.copy(), timeout=5, artifacts=tmp_path / "artifacts",
        )
    _assert_child_gone(tmp_path)


def test_launch_failure_does_not_leak_descriptors(tmp_path):
    before = len(list(pathlib.Path("/dev/fd").iterdir()))
    with pytest.raises(FileNotFoundError):
        mutate.run_phase_process(
            [str(tmp_path / "missing-command")], ownership_contract="inherited-token",
            cwd=tmp_path, environment=os.environ.copy(), timeout=5, artifacts=tmp_path / "artifacts",
        )
    assert len(list(pathlib.Path("/dev/fd").iterdir())) == before


def test_tokenless_detached_child_is_outside_diagnostic_contract(tmp_path):
    # This finite child is deliberate evidence of the remaining backend gap.
    child = "import pathlib,time; pathlib.Path('ready').write_text('ready'); time.sleep(1.5); pathlib.Path('outside').write_text('wrote')"
    parent = "\n".join([
        "import pathlib,subprocess,sys,time",
        f"child = subprocess.Popen([sys.executable, '-c', {child!r}], env={{}}, start_new_session=True)",
        "deadline = time.monotonic() + 3",
        "while not pathlib.Path('ready').exists():",
        "    if child.poll() is not None or time.monotonic() > deadline: raise RuntimeError('child not ready')",
        "    time.sleep(.005)",
    ])
    result = mutate.run_phase_process(
        [sys.executable, "-c", parent], ownership_contract="inherited-token",
        cwd=tmp_path, environment=os.environ.copy(), timeout=5, artifacts=tmp_path / "artifacts",
    )
    time.sleep(1.6)
    assert result.returncode == 0
    assert result.containment_error is None
    assert (tmp_path / "outside").exists(), "the counterexample no longer demonstrates the gap"
    print("LIMITATION tokenless detached child survived diagnostic cleanup; strict mode rejects before launch (Mac/APFS only)")

_POISON_CHANNELS = ("ignored", "tracked", "collateral", "index", "head", "bytecode", "child")


def _poison_program(channel, seed):
    # Each channel independently changes the verdict. No project imports are used.
    return "\n".join([
        "import importlib.machinery,os,pathlib,py_compile,subprocess,sys,time",
        "root = pathlib.Path.cwd()",
        "def git(*args): return subprocess.check_output(['git', *args], text=True).strip()",
        f"channel = {channel!r}",
        "if os.environ['MUTATION_PHASE'] == 'mutant':",
        "    dirty = {",
        "        'ignored': lambda: pathlib.Path('baseline.ignored').exists(),",
        "        'tracked': lambda: pathlib.Path('victim.py').read_text() != 'VALUE = 1\\n',",
        "        'collateral': lambda: pathlib.Path('collateral.py').read_text() != 'ORIGINAL\\n',",
        "        'index': lambda: git('show', ':victim.py') != 'VALUE = 1',",
        f"        'head': lambda: git('rev-parse', 'HEAD') != {seed!r},",
        "        'bytecode': lambda: pathlib.Path('explicit.pyc').exists() and importlib.machinery.SourcelessFileLoader('poison_cache', 'explicit.pyc').get_code('poison_cache') is not None,",
        "        'child': lambda: pathlib.Path('late.ignored').exists(),",
        "    }[channel]()",
        "    sys.exit(1 if dirty else 0)",
        "pathlib.Path('baseline.ignored').write_text('run-created')",
        "pathlib.Path('victim.py').write_text('VALUE = 99\\n')",
        "pathlib.Path('collateral.py').write_text('COLLATERAL DAMAGE\\n')",
        "py_compile.compile('victim.py', cfile='explicit.pyc', doraise=True)",
        "git('add', 'victim.py')",
        "git('commit', '-qm', 'phase poison')",
        "pathlib.Path('victim.py').write_text('VALUE = 77\\n')",
        "git('add', 'victim.py')",
        "pathlib.Path('victim.py').write_text('VALUE = 88\\n')",
        "child = \"import pathlib,time; pathlib.Path('child-ready.ignored').write_text('ready'); time.sleep(1.5); pathlib.Path('late.ignored').write_text('late')\"",
        "proc = subprocess.Popen([sys.executable, '-c', child], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
        "deadline = time.monotonic() + 3",
        "while not pathlib.Path('child-ready.ignored').exists():",
        "    if proc.poll() is not None or time.monotonic() > deadline: raise RuntimeError('child not ready')",
        "    time.sleep(.005)",
    ])


@pytest.mark.parametrize("channel", _POISON_CHANNELS)
def test_poison_suite_changes_a_shared_tree_verdict_but_not_an_isolated_one(tmp_path, channel):
    """Set CWNG_POISON_BOUNDARY=shared to observe this same assertion red."""
    repo, seed = _committed_repo(tmp_path)
    program = _poison_program(channel, seed)
    shared = os.environ.get("CWNG_POISON_BOUNDARY") == "shared"
    with mutate.IsolatedSweep.create(repo, seed, state_root=tmp_path / "state") as sweep:
        root = repo if shared else sweep.root
        def phase(kind):
            env = {**os.environ, "MUTATION_PHASE": kind}
            if shared:
                return subprocess.run([sys.executable, "-c", program], cwd=root, env=env,
                                      capture_output=True, text=True, timeout=5)
            sweep.scrub()
            result = mutate.run_phase_process(
                ownership_contract="inherited-token",
                argv=[sys.executable, "-c", program], cwd=root, environment=env,
                timeout=5, artifacts=sweep.entry / "artifacts",
            )
            assert result.containment_error is None
            sweep.scrub()
            return result
        # A clean selection passes before poison is introduced.
        clean = phase("mutant")
        baseline = phase("baseline")
        assert clean.returncode == baseline.returncode == 0, baseline.stderr
        time.sleep(1.6)
        mutant = phase("mutant")
        print(f"ORACLE boundary={'shared' if shared else 'isolated'} channel={channel} "
              f"clean={clean.returncode} baseline={baseline.returncode} mutant={mutant.returncode} "
              f"{'CONTAMINATING' if mutant.returncode else 'CLEAN'} (Mac/APFS only)")
        assert mutant.returncode == clean.returncode, f"CONTAMINATING: {channel} changed the verdict"


def test_backup_names_are_path_derived_not_basename():
    """cps/admin.py and cps/api/admin.py must not share a backup file."""
    a = mutate._backup_name("cps/admin.py")
    b = mutate._backup_name("cps/api/admin.py")
    assert a != b, (
        "same-basename modules collide, so restoring one writes the other's "
        "contents over it — this clobbered a 3,788-line module once already"
    )


def test_a_stale_anchor_is_an_error_never_a_verdict(tmp_path, monkeypatch):
    victim = tmp_path / "cps" / "thing.py"
    victim.parent.mkdir(parents=True)
    victim.write_text("value = 1\n")
    monkeypatch.setattr(mutate, "REPO", tmp_path)

    result = mutate.run_mutant("stale", "cps/thing.py", "NOT PRESENT", "x", ["ignored"])

    assert result["status"] == "ERROR", (
        "an unapplied mutant must never be reported as caught or survived — "
        "a green pytest summary is indistinguishable from a missed defect"
    )
    assert "NOT applied" in result["detail"]
    assert victim.read_text() == "value = 1\n", "the file was touched despite the error"


def test_an_ambiguous_anchor_is_also_an_error(tmp_path, monkeypatch):
    victim = tmp_path / "cps" / "thing.py"
    victim.parent.mkdir(parents=True)
    victim.write_text("x = 1\nx = 1\n")
    monkeypatch.setattr(mutate, "REPO", tmp_path)

    result = mutate.run_mutant("ambiguous", "cps/thing.py", "x = 1", "x = 2", ["ignored"])

    assert result["status"] == "ERROR" and "matched 2 times" in result["detail"], (
        "replacing the first of several identical anchors mutates a line the "
        "author did not choose"
    )


def test_a_surviving_mutant_is_reported_and_the_file_is_restored(tmp_path, monkeypatch):
    victim = tmp_path / "cps" / "thing.py"
    victim.parent.mkdir(parents=True)
    original = "GUARD = True\n"
    victim.write_text(original)
    monkeypatch.setattr(mutate, "REPO", tmp_path)
    # A suite that passes no matter what the code says: the mutant survives.
    monkeypatch.setattr(mutate.subprocess, "run",
                        lambda *a, **k: type("P", (), {"returncode": 0, "stdout": "1 passed"})())

    result = mutate.run_mutant("survivor", "cps/thing.py", "GUARD = True",
                               "GUARD = False", ["ignored"])

    assert result["status"] == "SURVIVED", "a mutant the suite ignores must be reported, loudly"
    assert victim.read_text() == original, "the harness left the mutation in the working tree"
