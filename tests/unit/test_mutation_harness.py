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

_HARNESS = pathlib.Path(os.environ.get("CWNG_CHECK_MUTANT",
    str(pathlib.Path(__file__).resolve().parents[1] / "mutation" / "mutate.py")))
_spec = importlib.util.spec_from_file_location("mutate", _HARNESS)
mutate = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mutate
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
    (repo / "cps").mkdir()
    (repo / "cps" / "__init__.py").write_text("# Synthetic import fixture.\n")
    (repo / "cps" / "main.py").write_text("def main(): raise RuntimeError('application must not start')\n")
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


def test_sweep_scrubs_after_cleanup_and_again_before_each_phase(tmp_path, monkeypatch):
    repo, seed = _committed_repo(tmp_path)
    events = []
    terminate = mutate._terminate_phase_processes
    def observed_cleanup(proc, token):
        result = terminate(proc, token)
        events.append("terminated")
        return result
    monkeypatch.setattr(mutate, "_terminate_phase_processes", observed_cleanup)
    with mutate.IsolatedSweep.create(repo, seed, state_root=tmp_path / "state") as sweep:
        scrub = sweep.scrub
        def observed_scrub():
            result = scrub()
            events.append("scrubbed")
            return result
        monkeypatch.setattr(sweep, "scrub", observed_scrub)
        program = "\n".join([
            "import os,pathlib",
            "assert pathlib.Path('victim.py').read_text() == 'VALUE = 1\\n'",
            "assert not pathlib.Path('between.ignored').exists()",
            "assert os.getpgrp() == os.getpid()",
            "pathlib.Path('victim.py').write_text('phase changed it')",
            "print(os.getpid())",
        ])
        leaders = []
        for _ in range(2):
            (sweep.root / "between.ignored").write_text("between phases")
            result = sweep.run_phase(
                [sys.executable, "-c", program], environment=os.environ.copy(),
                timeout=5, ownership_contract="inherited-token",
            )
            assert result.returncode == 0, result.stderr
            leaders.append(int(result.stdout))
            assert (sweep.root / "victim.py").read_text() == "VALUE = 1\n"
            assert not (sweep.root / "between.ignored").exists()
        assert leaders[0] != leaders[1]
        assert events == (["scrubbed"] + ["terminated"] * 4 + ["scrubbed"]) * 2


@pytest.mark.parametrize("failure", ["escape", "timeout"])
def test_sweep_errors_stop_the_next_phase(tmp_path, failure):
    repo, seed = _committed_repo(tmp_path)
    with mutate.IsolatedSweep.create(repo, seed, state_root=tmp_path / "state") as sweep:
        program = _child_program(detach=failure == "escape", timeout=failure == "timeout")
        with pytest.raises(mutate.IsolationError, match="escaped|timed out"):
            sweep.run_phase(
                [sys.executable, "-c", program], environment=os.environ.copy(),
                timeout=1 if failure == "timeout" else 5, ownership_contract="inherited-token",
            )
        with pytest.raises(mutate.IsolationError, match="after an error"):
            sweep.run_phase(
                [sys.executable, "-c", "raise RuntimeError('must not run')"],
                environment=os.environ.copy(), timeout=5, ownership_contract="inherited-token",
            )

_POISON_CHANNELS = ("ignored", "tracked", "collateral", "index", "head", "bytecode", "child")


def _poison_program(channel, seed):
    # Each channel independently changes the verdict. No project imports are used.
    return "\n".join([
        "import importlib.machinery,os,pathlib,py_compile,subprocess,sys,time",
        "root = pathlib.Path.cwd()",
        "gate = root.parent / 'poison-release'",
        "def cached_value_changed():",
        "    if not pathlib.Path('explicit.pyc').exists(): return False",
        "    values = {}",
        "    exec(importlib.machinery.SourcelessFileLoader('poison_cache', 'explicit.pyc').get_code('poison_cache'), values)",
        "    return values['VALUE'] != 1",
        "def git(*args): return subprocess.check_output(['git', *args], text=True).strip()",
        f"channel = {channel!r}",
        "if os.environ['MUTATION_PHASE'] == 'mutant':",
        "    if channel == 'child':",
        "        gate.write_text('mutant has started')",
        "        deadline = time.monotonic() + 1.5",
        "        while not pathlib.Path('late.ignored').exists() and time.monotonic() < deadline: time.sleep(.005)",
        "    dirty = {",
        "        'ignored': lambda: pathlib.Path('baseline.ignored').exists(),",
        "        'tracked': lambda: pathlib.Path('victim.py').read_text() != 'VALUE = 1\\n',",
        "        'collateral': lambda: pathlib.Path('collateral.py').read_text() != 'ORIGINAL\\n',",
        "        'index': lambda: git('show', ':victim.py') != 'VALUE = 1',",
        f"        'head': lambda: git('rev-parse', 'HEAD') != {seed!r},",
        "        'bytecode': cached_value_changed,",
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
        "gate.unlink(missing_ok=True)",
        'child = "import pathlib,time; pathlib.Path(\'child-ready.ignored\').write_text(\'ready\'); gate=pathlib.Path(\'..\') / \'poison-release\'; deadline=time.monotonic()+5\\nwhile not gate.exists() and time.monotonic()<deadline: time.sleep(.005)\\nif gate.exists(): pathlib.Path(\'late.ignored\').write_text(\'late\')"',
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
            return sweep.run_phase(
                [sys.executable, "-c", program], environment=env, timeout=5,
                ownership_contract="inherited-token",
            )
        # A clean selection passes before poison is introduced.
        clean = phase("mutant")
        baseline = phase("baseline")
        assert clean.returncode == baseline.returncode == 0, baseline.stderr
        if channel == "child" and shared:
            _git(root, "reset", "--hard", seed)
            _git(root, "clean", "-ffdx")
            assert _git(root, "status", "--porcelain", "--ignored") == ""
            print("CHILD_BOUNDARY shared tree scrubbed clean before mutant releases writer (Mac/APFS only)")
        if channel != "child":
            (root.parent / "poison-release").write_text("release finite fixture child")
            time.sleep(.1)
        mutant = phase("mutant")
        print(f"ORACLE boundary={'shared' if shared else 'isolated-diagnostic'} channel={channel} "
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

    assert result["status"] == "UNVERIFIED", "a diagnostic observation must not become a verdict"
    assert result["returncode"] == 0
    assert victim.read_text() == original, "the harness left the mutation in the working tree"


def test_provenance_environment_pins_root_without_mutating_input(tmp_path):
    env = {"PYTHONPATH": "relative-path" + os.pathsep + str(tmp_path), "KEEP": "yes"}
    before = env.copy()
    prepared = mutate.provenance_environment(tmp_path, env)
    assert env == before
    assert prepared["PYTHONPATH"].split(os.pathsep) == [str(tmp_path.resolve()), str(pathlib.Path('relative-path').resolve())]
    assert prepared["KEEP"] == "yes"


def test_real_cps_provenance_all_three_shapes(tmp_path):
    seed = _git(mutate.REPO, 'rev-parse', 'HEAD')
    with mutate.IsolatedSweep.create(mutate.REPO, seed, state_root=tmp_path / 'state') as sweep:
        env = mutate.provenance_environment(sweep.root, os.environ.copy())
        records = mutate.provenance_preflight(
            sweep.root, environment=env, artifacts=sweep.entry / 'probe',
            pytest_targets=['tests/unit/test_mutation_harness.py'],
        )
        assert [record['shape'] for record in records] == ['pytest', 'child', 'console']
        for record in records:
            assert record['paths'][0] == 'cps/__init__.py'
            print(f"PROVENANCE shape={record['shape']} path={record['paths'][0]} ACCEPTED (Mac/APFS only)")
        assert records[-1]['paths'][-1] == 'cps/main.py'
        # Removing PYTHONPATH reproduces the installed editable checkout escape.
        broken = {**os.environ, 'PYTHONPATH': ''}
        with pytest.raises(mutate.IsolationError, match='child resolved outside disposable root') as error:
            mutate.provenance_preflight(sweep.root, environment=broken, artifacts=sweep.entry / 'negative')
        print('PROVENANCE installed-checkout negative control: ' + str(error.value) + ' (Mac/APFS only)')


@pytest.mark.parametrize('shape', ['pytest', 'child', 'console'])
def test_provenance_rejects_each_outside_import(tmp_path, shape):
    repo, seed = _committed_repo(tmp_path)
    foreign = tmp_path / 'foreign'
    (foreign / 'cps').mkdir(parents=True)
    (foreign / 'cps' / '__init__.py').write_text('# Foreign import fixture.\n')
    (foreign / 'cps' / 'main.py').write_text("def main(): raise RuntimeError('must not start')\n")
    with mutate.IsolatedSweep.create(repo, seed, state_root=tmp_path / 'state') as sweep:
        env = mutate.provenance_environment(sweep.root, {**os.environ, 'FOREIGN_ROOT': str(foreign)})
        kwargs = {}
        if shape == 'pytest':
            (foreign / 'conftest.py').write_text("import os,sys\nsys.path.insert(0, os.environ['FOREIGN_ROOT'])\nimport cps\n")
            (foreign / 'test_empty.py').write_text('# Collection only.\n')
            kwargs['pytest_targets'] = [str(foreign / 'test_empty.py')]
        elif shape == 'child':
            env['PYTHONPATH'] = str(foreign)
        else:
            launcher = foreign / 'console'
            launcher.write_text("import os,sys\nsys.path.insert(0, os.environ['FOREIGN_ROOT'])\nfrom cps.main import main\nmain()\n")
            kwargs['console'] = launcher
        with pytest.raises(mutate.IsolationError, match=shape + ' resolved outside disposable root') as error:
            mutate.provenance_preflight(sweep.root, environment=env, artifacts=sweep.entry / 'probe', **kwargs)
        print(f"PROVENANCE negative shape={shape}: {error.value} (Mac/APFS only)")


def test_provenance_rejection_prevents_phase_execution(tmp_path, monkeypatch):
    repo, seed = _committed_repo(tmp_path)
    monkeypatch.setattr(mutate, 'provenance_environment', lambda root, env: {**env, 'PYTHONPATH': ''})
    with mutate.IsolatedSweep.create(repo, seed, state_root=tmp_path / 'state') as sweep:
        with pytest.raises(mutate.IsolationError, match='provenance REJECTED'):
            sweep.run_phase(
                [sys.executable, '-c', "from pathlib import Path; Path('must-not-run').touch()"],
                environment=os.environ.copy(), timeout=5, ownership_contract='inherited-token',
            )
        assert not (sweep.root / 'must-not-run').exists()
        assert sweep._phase_failed


@pytest.mark.parametrize('returncode', [0, 1])
def test_diagnostic_labelling_cannot_be_bypassed(tmp_path, monkeypatch, returncode):
    import dataclasses
    victim = tmp_path / 'victim.py'
    victim.write_text('VALUE = 1\n')
    monkeypatch.setattr(mutate, 'REPO', tmp_path)
    monkeypatch.setattr(mutate.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(
        a[0], returncode, stdout='1 passed' if returncode == 0 else '1 failed', stderr=''))
    result = mutate.run_mutant('diagnostic', 'victim.py', 'VALUE = 1', 'VALUE = 2', ['unused'])
    assert result['status'] == 'UNVERIFIED', 'weak backend emitted an authoritative verdict'
    assert result['authoritative'] is False
    assert result['returncode'] == returncode
    with pytest.raises((TypeError, AttributeError)):
        result['status'] = 'caught'
    with pytest.raises((TypeError, ValueError)):
        dataclasses.replace(result, status='SURVIVED')
    with pytest.raises((TypeError, AttributeError)):
        result.status = 'caught'
    assert dataclasses.asdict(result)['status'] == 'UNVERIFIED'
    assert not hasattr(result, '__dict__')
    with pytest.raises(TypeError):
        mutate.DiagnosticObservation('forged', 0, 'observation', status='caught')
    assert victim.read_text() == 'VALUE = 1\n'
    print(f'LABEL observation exit={returncode} UNVERIFIED immutable (Mac/APFS only)')


def test_phase_result_cannot_gain_authority(tmp_path):
    import dataclasses
    result = mutate.run_phase_process(
        [sys.executable, '-c', 'pass'], cwd=tmp_path, environment=os.environ.copy(),
        timeout=5, artifacts=tmp_path / 'artifacts', ownership_contract='inherited-token',
    )
    assert getattr(result, 'status', None) == 'UNVERIFIED', 'raw backend result lost diagnostic label'
    assert result.authoritative is False
    assert dataclasses.asdict(result)['status'] == 'UNVERIFIED'
    with pytest.raises((TypeError, ValueError)):
        dataclasses.replace(result, authoritative=True)


@pytest.mark.parametrize('legacy_label', ['caught', 'SURVIVED'])
def test_terminal_cannot_promote_diagnostic_results(monkeypatch, capsys, legacy_label):
    monkeypatch.setattr(sys, 'argv', ['mutate.py', '--file', 'unused', '--old', 'a', '--new', 'b', '--test', 'unused'])
    monkeypatch.setattr(mutate, 'run_mutant', lambda *a, **k: {
        'name': 'diagnostic', 'status': legacy_label, 'summary': 'observation only',
    })
    code = mutate.main()
    output = capsys.readouterr().out
    assert code != 0, 'diagnostic output must not satisfy an authoritative gate'
    assert 'UNVERIFIED' in output
    assert 'caught' not in output and 'SURVIVED' not in output


@pytest.mark.parametrize('observed_exit', [0, 1])
def test_real_cli_observations_never_satisfy_authoritative_gate(tmp_path, observed_exit):
    harness = tmp_path / 'tests' / 'mutation' / 'mutate.py'
    harness.parent.mkdir(parents=True)
    harness.write_bytes(_HARNESS.read_bytes())
    victim = tmp_path / 'victim.py'
    victim.write_text('VALUE = 1\n')
    (tmp_path / 'test_result.py').write_text(
        'from victim import VALUE\ndef test_value(): assert VALUE == ' + ('2' if observed_exit == 0 else '1') + '\n')
    result = subprocess.run(
        [sys.executable, str(harness), '--file', 'victim.py', '--old', 'VALUE = 1',
         '--new', 'VALUE = 2', '--test', 'test_result.py'],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
        env={**os.environ, 'PYTEST_ADDOPTS': '', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
    )
    assert result.returncode == 1, result.stderr
    assert '1 passed' in result.stdout if observed_exit == 0 else '1 failed' in result.stdout
    assert 'UNVERIFIED' in result.stdout
    assert 'caught' not in result.stdout and 'SURVIVED' not in result.stdout
    assert victim.read_text() == 'VALUE = 1\n'
    print(f'LABEL real-cli observed_exit={observed_exit} terminal_exit=1 UNVERIFIED (Mac/APFS only)')


@pytest.mark.parametrize('source,old,new', [
    (b'VALUE = 1\n', 'absent', '2'),
    (b'x x\n', 'x', 'y'),
    (b'VALUE = 1\n', '1', '1'),
    (b'', '', 'new'),
])
def test_integrity_refuses_invalid_plan_before_execution(tmp_path, source, old, new):
    target = tmp_path / 'victim.py'
    target.write_bytes(source)
    with pytest.raises(mutate.IsolationError):
        mutate.prepare_mutation(tmp_path, 'victim.py', old, new)
    assert target.read_bytes() == source


def test_integrity_preserves_bytes_and_refuses_stale_or_noop_application(tmp_path):
    target = tmp_path / 'victim.py'
    target.write_bytes(b'VALUE = 1\r\n')
    plan = mutate.prepare_mutation(tmp_path, 'victim.py', '1', '2')
    assert plan.after == b'VALUE = 2\r\n'
    target.write_bytes(b'changed after preparation\n')
    with pytest.raises(mutate.IsolationError):
        mutate.apply_mutation(tmp_path, plan)
    target.write_bytes(plan.before)
    with pytest.raises(mutate.IsolationError):
        mutate.apply_mutation(tmp_path, mutate.MutationPlan('victim.py', plan.before, plan.before))
    mutate.apply_mutation(tmp_path, plan)
    assert target.read_bytes() == plan.after


def _collection_fixture(tmp_path):
    (tmp_path / 'test_probe.py').write_text('def test_one(): pass\ndef test_two(): pass\n')
    nodes = ['test_probe.py::test_one', 'test_probe.py::test_two']
    phase = mutate.PhaseResult((), 0, '2 tests collected in 0.01s\n', '', False, None, ())
    report = {'version': 1, 'complete': True, 'exitstatus': 0, 'selected': nodes,
              'selected_count': 2, 'deselected': [], 'collection_errors': [], 'reports': []}
    return phase, report


@pytest.mark.parametrize('defect', ['empty', 'fake_node', 'missing_file', 'duplicate', 'count',
    'numerator', 'denominator', 'collection_error', 'exit', 'missing_summary', 'malformed_summary',
    'incomplete', 'missing_report', 'setup_error', 'numerator_only', 'schema_version'])
def test_collection_accounting_rejects_unsound_selection(tmp_path, defect):
    from dataclasses import replace
    phase, report = _collection_fixture(tmp_path)
    if defect == 'empty': report.update(selected=[], selected_count=0)
    if defect == 'fake_node': report['selected'] = ['not-a-node', report['selected'][1]]
    if defect == 'missing_file': report['selected'][0] = 'missing.py::test_one'
    if defect == 'duplicate': report['selected'][1] = report['selected'][0]
    if defect == 'count': report['selected_count'] = 3
    if defect == 'numerator': phase = replace(phase, stdout='1/2 tests collected (1 deselected) in 0.01s\n')
    if defect == 'numerator_only': phase = replace(phase, stdout='1 test collected in 0.01s\n')
    if defect == 'schema_version': report['version'] = 2
    if defect == 'denominator': phase = replace(phase, stdout='2/3 tests collected (1 deselected) in 0.01s\n')
    if defect == 'collection_error': report['collection_errors'] = ['test_broken.py']
    if defect == 'exit': phase = replace(phase, returncode=2)
    if defect == 'missing_summary': phase = replace(phase, stdout='test_probe.py::test_one\n')
    if defect == 'malformed_summary': phase = replace(phase, stdout='many tests collected in 0.01s\n')
    if defect == 'incomplete': report['complete'] = False
    if defect == 'missing_report': report = {}
    if defect == 'setup_error': report['reports'] = [{'nodeid': report['selected'][0], 'when': 'setup', 'outcome': 'failed'}]
    with pytest.raises(mutate.IsolationError):
        mutate.validate_collection(tmp_path, phase, report)


def test_collection_accounting_accepts_selected_numerator(tmp_path):
    from dataclasses import replace
    phase, report = _collection_fixture(tmp_path)
    report['deselected'] = ['test_probe.py::test_other']
    phase = replace(phase, stdout='2/3 tests collected (1 deselected) in 0.01s\n')
    assert mutate.validate_collection(tmp_path, phase, report) == tuple(report['selected'])


def test_collection_real_pytest_selected_numerator(tmp_path):
    repo, seed = _committed_repo(tmp_path)
    (repo / 'test_probe.py').write_text('def test_one(): pass\ndef test_two(): pass\ndef test_other(): pass\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'collection fixture')
    with mutate.IsolatedSweep.create(repo, 'HEAD', state_root=tmp_path / 'state') as sweep:
        phase, report = mutate._run_pytest(sweep, ['test_probe.py', '-k', 'not other'],
            {**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}, 10, collect_only=True)
        nodes = mutate.validate_collection(sweep.root, phase, report)
        assert len(nodes) == 2
        assert '2/3 tests collected' in phase.stdout
        print('ACCOUNTING real pytest: 2/3 tests collected; selected=2 ACCEPTED (Mac/APFS only)')


def _execution_fixture(tmp_path):
    from dataclasses import replace
    phase, report = _collection_fixture(tmp_path)
    phase = replace(phase, stdout='2 passed in 0.01s\n')
    report['reports'] = [{'nodeid': node, 'when': when, 'outcome': 'passed', 'wasxfail': False}
                         for node in report['selected'] for when in ('setup', 'call', 'teardown')]
    return phase, report, tuple(report['selected'])


@pytest.mark.parametrize('code', [2, 3, 4, 5, 6, -9, 255])
def test_execution_rejects_infrastructure_exit_codes(tmp_path, code):
    from dataclasses import replace
    phase, report, nodes = _execution_fixture(tmp_path)
    phase = replace(phase, returncode=code)
    report['exitstatus'] = code
    with pytest.raises(mutate.IsolationError):
        mutate.validate_execution(phase, report, nodes)


@pytest.mark.parametrize('defect', ['missing_summary', 'malformed_summary', 'wrong_count', 'duplicate_summary',
    'exit_one_without_failure', 'exit_zero_with_failure', 'summary_not_actual', 'missing_selected',
    'missing_call', 'duplicate_call', 'foreign_report', 'baseline_failure'])
def test_execution_summary_and_reality_guards(tmp_path, defect):
    from dataclasses import replace
    phase, report, nodes = _execution_fixture(tmp_path)
    if defect == 'missing_summary': phase = replace(phase, stdout='')
    if defect == 'malformed_summary': phase = replace(phase, stdout='2 passed, nonsense in 0.01s\n')
    if defect == 'wrong_count': phase = replace(phase, stdout='1 passed in 0.01s\n')
    if defect == 'duplicate_summary': phase = replace(phase, stdout='1 passed, 1 passed in 0.01s\n')
    if defect == 'exit_one_without_failure':
        phase = replace(phase, returncode=1)
        report['exitstatus'] = 1
    if defect in ('exit_zero_with_failure', 'summary_not_actual', 'baseline_failure'):
        phase = replace(phase, stdout='1 failed, 1 passed in 0.01s\n',
                        returncode=0 if defect == 'exit_zero_with_failure' else 1)
        report['exitstatus'] = phase.returncode
        if defect != 'summary_not_actual': report['reports'][1]['outcome'] = 'failed'
    if defect == 'missing_selected':
        report.update(selected=[nodes[0]], selected_count=1, reports=report['reports'][:3])
        phase = replace(phase, stdout='1 passed in 0.01s\n')
    if defect == 'missing_call': report['reports'].pop(1)
    if defect == 'duplicate_call': report['reports'].append(report['reports'][1].copy())
    if defect == 'foreign_report': report['reports'][1]['nodeid'] = 'test_probe.py::foreign'
    with pytest.raises(mutate.IsolationError):
        mutate.validate_execution(phase, report, nodes, baseline=defect == 'baseline_failure')


def _soundness_repo(tmp_path, *, broken=False, body=None):
    repo, seed = _committed_repo(tmp_path)
    (repo / 'victim.py').write_text('VALUE = ' + ('2' if broken else '1') + '\n')
    (repo / 'test_probe.py').write_text(body or 'from victim import VALUE\ndef test_value(): assert VALUE == 1\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'soundness fixture')
    return repo


def test_execution_real_broken_baseline_stops_mutant(tmp_path):
    repo = _soundness_repo(tmp_path, broken=True)
    trace = []
    with mutate.IsolatedSweep.create(repo, 'HEAD', state_root=tmp_path / 'state') as sweep:
        with pytest.raises(mutate.IsolationError):
            mutate._assess_mutation(sweep, 'victim.py', '2', '1', ['test_probe.py'],
                {**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}, 10, trace)
        assert [name for name, _, _ in trace] == ['collection', 'baseline']


def test_execution_real_clean_baseline_and_visible_mutation(tmp_path):
    repo = _soundness_repo(tmp_path)
    trace = []
    with mutate.IsolatedSweep.create(repo, 'HEAD', state_root=tmp_path / 'state') as sweep:
        check = mutate._assess_mutation(sweep, 'victim.py', '1', '2', ['test_probe.py'],
            {**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}, 10, trace)
        assert [phase.returncode for _, phase, _ in trace] == [0, 0, 1]
        assert check.signal == 'TEST_FAILURE' and check.failures == 1
        assert check.status == 'UNVERIFIED' and check.authoritative is False
        assert (sweep.root / 'victim.py').read_text() == 'VALUE = 1\n'
        print('SOUNDNESS clean baseline=0 mutant=1 actual_failures=1 UNVERIFIED (Mac/APFS only)')


def _xfail_fixture(tmp_path, *, run, mixed=False):
    from dataclasses import replace
    phase, report, nodes = _execution_fixture(tmp_path)
    for node in nodes[:1] if mixed else nodes:
        if run:
            for event in report['reports']:
                if event['nodeid'] == node and event['when'] == 'call':
                    event.update(outcome='skipped', wasxfail=True)
        else:
            report['reports'] = [e for e in report['reports'] if not (e['nodeid'] == node and e['when'] == 'call')]
            for event in report['reports']:
                if event['nodeid'] == node and event['when'] == 'setup':
                    event.update(outcome='skipped', wasxfail=True)
    phase = replace(phase, stdout=('1 passed, 1 xfailed' if mixed else '2 xfailed') + ' in 0.01s\n')
    return phase, report, nodes


@pytest.mark.parametrize('run', [False, True])
def test_execution_all_xfailed_has_no_outcome(tmp_path, run):
    phase, report, nodes = _xfail_fixture(tmp_path, run=run)
    with pytest.raises(mutate.IsolationError):
        mutate.validate_execution(phase, report, nodes)


def test_execution_xfail_notrun_is_not_a_body_execution(tmp_path):
    phase, report, nodes = _xfail_fixture(tmp_path, run=False, mixed=True)
    check = mutate.validate_execution(phase, report, nodes)
    assert check.executed == 1, 'xfail(run=False) was counted as executed'


def _mock_assessment(monkeypatch):
    monkeypatch.setattr(mutate, '_assess_mutation', lambda *a, **k: mutate.ExecutionCheck('TESTS_PASSED', 1, 0))


def test_durable_evidence_precedes_return_and_presentation(tmp_path, monkeypatch, capsys):
    repo, _ = _committed_repo(tmp_path)
    _mock_assessment(monkeypatch)
    with mutate.IsolatedSweep.create(repo, 'HEAD', state_root=tmp_path / 'state') as sweep:
        events = []
        sync, rename, control = mutate.os.fsync, mutate.os.replace, mutate.fcntl.fcntl
        def synced(fd): events.append('sync'); return sync(fd)
        def renamed(*args): events.append('rename'); return rename(*args)
        def controlled(fd, command, *args):
            if command == mutate.fcntl.F_FULLFSYNC: events.append('fullsync')
            return control(fd, command, *args)
        monkeypatch.setattr(mutate.os, 'fsync', synced)
        monkeypatch.setattr(mutate.os, 'replace', renamed)
        monkeypatch.setattr(mutate.fcntl, 'fcntl', controlled)
        result = mutate.run_checked_mutation(sweep, 'victim.py', '1', '2', ['unused'],
            environment=os.environ.copy(), timeout=5, evidence_dir=tmp_path / 'evidence')
        assert events == ['sync', 'rename', 'sync', 'fullsync'], 'result returned before durable publication'
        assert capsys.readouterr().out == ''
        assert mutate.present_checked_result(result) == 1
        assert 'UNVERIFIED' in capsys.readouterr().out
    assert result.evidence.is_file(), 'sweep teardown removed the evidence'


def test_durability_failure_does_not_present_a_result(tmp_path, monkeypatch, capsys):
    repo, _ = _committed_repo(tmp_path)
    _mock_assessment(monkeypatch)
    with mutate.IsolatedSweep.create(repo, 'HEAD', state_root=tmp_path / 'state') as sweep:
        def broken(fd): raise OSError('injected sync failure')
        monkeypatch.setattr(mutate.os, 'fsync', broken)
        with pytest.raises(mutate.IsolationError):
            result = mutate.run_checked_mutation(sweep, 'victim.py', '1', '2', ['unused'],
                environment=os.environ.copy(), timeout=5, evidence_dir=tmp_path / 'evidence')
            mutate.present_checked_result(result)
        assert capsys.readouterr().out == ''


def test_evidence_cannot_be_disposable_or_changed_before_presentation(tmp_path, monkeypatch):
    repo, _ = _committed_repo(tmp_path)
    _mock_assessment(monkeypatch)
    with mutate.IsolatedSweep.create(repo, 'HEAD', state_root=tmp_path / 'state') as sweep:
        with pytest.raises(mutate.IsolationError):
            mutate.run_checked_mutation(sweep, 'victim.py', '1', '2', ['unused'],
                environment=os.environ.copy(), timeout=5, evidence_dir=sweep.entry / 'evidence')
        result = mutate.run_checked_mutation(sweep, 'victim.py', '1', '2', ['unused'],
            environment=os.environ.copy(), timeout=5, evidence_dir=tmp_path / 'evidence')
        result.evidence.write_text('{}')
        with pytest.raises(mutate.IsolationError):
            mutate.present_checked_result(result)


@pytest.mark.parametrize('kind', ['xfail_notrun', 'xfail_run', 'setup_error', 'timeout', 'noop', 'pass'])
def test_checked_real_outcomes_are_durable_and_unverified(tmp_path, kind):
    bodies = {
        'xfail_notrun': "import pytest\n@pytest.mark.xfail(run=False)\ndef test_value(): raise AssertionError('must not execute')\n",
        'xfail_run': "import pytest\n@pytest.mark.xfail\ndef test_value(): assert False\n",
        'setup_error': "import pytest\n@pytest.fixture\ndef broken(): raise RuntimeError('fixture failure')\ndef test_value(broken): pass\n",
        'timeout': "import time\ndef test_value(): time.sleep(2)\n",
        'pass': "def test_value(): assert True\n",
    }
    repo = _soundness_repo(tmp_path, body=bodies.get(kind))
    with mutate.IsolatedSweep.create(repo, 'HEAD', state_root=tmp_path / 'state') as sweep:
        result = mutate.run_checked_mutation(sweep, 'victim.py', '1', '1' if kind == 'noop' else '2',
            ['test_probe.py'], environment={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
            timeout=.05 if kind == 'timeout' else 10, evidence_dir=tmp_path / 'evidence')
        assert result.status == 'UNVERIFIED' and result.authoritative is False
        assert result.exit_code == 1
        assert result.signal == ('TESTS_PASSED' if kind == 'pass' else 'ERROR'), result.detail
        reasons = {'xfail_notrun': 'no ordinary', 'xfail_run': 'no ordinary',
                   'setup_error': 'setup', 'timeout': 'timed out', 'noop': 'no-op'}
        if kind in reasons:
            assert reasons[kind] in result.detail, result.detail
        if kind == 'pass':
            phases = json.loads(result.evidence.read_text())['phases']
            assert len(phases) == 3
            assert all(phase['returncode'] == 0 for phase in phases)
        if kind == 'noop': assert json.loads(result.evidence.read_text())['phases'] == []
    payload = json.loads(result.evidence.read_text())
    assert payload['signal'] == result.signal
    assert payload['status'] == 'UNVERIFIED'
    print(f'SOUNDNESS real {kind}: {result.signal} UNVERIFIED durable (Mac/APFS only)')


def test_noop_is_refused_before_any_pytest_collection(tmp_path, monkeypatch):
    repo, _ = _committed_repo(tmp_path)
    def forbidden(*a, **k): pytest.fail('pytest ran for a no-op mutation')
    monkeypatch.setattr(mutate, '_run_pytest', forbidden)
    with mutate.IsolatedSweep.create(repo, 'HEAD', state_root=tmp_path / 'state') as sweep:
        with pytest.raises(mutate.IsolationError, match='no-op'):
            mutate._assess_mutation(sweep, 'victim.py', '1', '1', ['unused'], os.environ.copy(), 5, [])


def test_integrity_detects_incomplete_write(tmp_path, monkeypatch):
    target = tmp_path / 'victim.py'
    target.write_bytes(b'VALUE = 1\n')
    plan = mutate.prepare_mutation(tmp_path, 'victim.py', '1', '2')
    original = pathlib.Path.write_bytes
    def truncated(path, data):
        return original(path, data[:-1] if path == target else data)
    monkeypatch.setattr(pathlib.Path, 'write_bytes', truncated)
    with pytest.raises(mutate.IsolationError, match='requested bytes'):
        mutate.apply_mutation(tmp_path, plan)
