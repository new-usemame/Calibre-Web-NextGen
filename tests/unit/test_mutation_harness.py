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
import signal
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.unit

# Test-only proxy exercises the Linux selection and backend rejection on macOS.
TEST_PLATFORM = os.environ.get("CWNG_TEST_PLATFORM", sys.platform)
DARWIN_REASON = "requires macOS diagnostic process backend"
DARWIN_ONLY = pytest.mark.skipif(TEST_PLATFORM != "darwin", reason=DARWIN_REASON)


@pytest.fixture(autouse=True)
def _platform_proxy(monkeypatch):
    if TEST_PLATFORM == "linux" and sys.platform != "linux":
        from types import SimpleNamespace
        monkeypatch.setattr(mutate, "sys", SimpleNamespace(**{**vars(sys), "platform": "linux"}))
        def forbidden(*a, **k):
            pytest.fail("Linux proxy reached a Darwin process syscall")
        monkeypatch.setattr(mutate, "_process_identity", forbidden)
        monkeypatch.setattr(mutate, "_has_phase_token", forbidden)


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


STARTUP_TIMEOUT = 60  # Bounded hang watchdog; interpreter startup is not a benchmark.


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
        "deadline = time.monotonic() + 30",
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


@DARWIN_ONLY
def test_phase_exit_kills_a_child_that_would_write_later(tmp_path):
    result = mutate.run_phase_process(
        ownership_contract="inherited-token",
        argv=[sys.executable, "-c", _child_program()], cwd=tmp_path,
        environment=os.environ.copy(), timeout=STARTUP_TIMEOUT, artifacts=tmp_path / "artifacts",
    )
    assert "child ready" in result.stdout
    assert result.returncode == 0
    assert result.containment_error is None
    _assert_child_gone(tmp_path)


@DARWIN_ONLY
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


@DARWIN_ONLY
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


@DARWIN_ONLY
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


@DARWIN_ONLY
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


@DARWIN_ONLY
def test_launch_failure_does_not_leak_descriptors(tmp_path):
    before = len(list(pathlib.Path("/dev/fd").iterdir()))
    with pytest.raises(FileNotFoundError):
        mutate.run_phase_process(
            [str(tmp_path / "missing-command")], ownership_contract="inherited-token",
            cwd=tmp_path, environment=os.environ.copy(), timeout=5, artifacts=tmp_path / "artifacts",
        )
    assert len(list(pathlib.Path("/dev/fd").iterdir())) == before


@DARWIN_ONLY
def test_tokenless_detached_child_is_outside_diagnostic_contract(tmp_path):
    # This finite child is deliberate evidence of the remaining backend gap.
    child = "import pathlib,time; pathlib.Path('ready').write_text('ready'); time.sleep(1.5); pathlib.Path('outside').write_text('wrote')"
    parent = "\n".join([
        "import pathlib,subprocess,sys,time",
        f"child = subprocess.Popen([sys.executable, '-c', {child!r}], env={{}}, start_new_session=True)",
        "deadline = time.monotonic() + 30",
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


@DARWIN_ONLY
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
        assert events == (["scrubbed"] + ["terminated"] * 3 + ["scrubbed", "terminated", "scrubbed"]) * 2


@pytest.mark.parametrize("failure", ["escape", "timeout"])
@DARWIN_ONLY
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
        "deadline = time.monotonic() + 30",
        "while not pathlib.Path('child-ready.ignored').exists():",
        "    if proc.poll() is not None or time.monotonic() > deadline: raise RuntimeError('child not ready')",
        "    time.sleep(.005)",
    ])


@pytest.mark.parametrize("channel", _POISON_CHANNELS)
@DARWIN_ONLY
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
                                      capture_output=True, text=True, timeout=STARTUP_TIMEOUT)
            return sweep.run_phase(
                [sys.executable, "-c", program], environment=env, timeout=STARTUP_TIMEOUT,
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


def test_old_direct_mutation_route_is_removed():
    assert not hasattr(mutate, 'run_mutant')
    assert not hasattr(mutate, '_backup_name')
    assert not hasattr(mutate, 'DiagnosticObservation')


@pytest.mark.parametrize('old,body', [('NOT PRESENT', 'VALUE = 1\n'), ('1', 'VALUE = 1 + 1\n')])
def test_isolated_invalid_anchor_preserves_source(tmp_path, old, body):
    repo, seed = _committed_repo(tmp_path)
    (repo / 'victim.py').write_text(body)
    _git(repo, 'add', '.')
    _git(repo, 'commit', '--allow-empty', '-qm', 'anchor fixture')
    with mutate.IsolatedSweep.create(repo, 'HEAD', state_root=tmp_path / 'state') as sweep:
        result = mutate.run_checked_mutation(sweep, 'victim.py', old, '2', ['unused'],
            environment=os.environ.copy(), timeout=60, evidence_dir=tmp_path / 'evidence')
        assert result.signal == 'ERROR' and 'exactly once' in result.detail
        assert (repo / 'victim.py').read_text() == body
        assert json.loads(result.evidence.read_text())['phases'] == []


@DARWIN_ONLY
def test_isolated_passing_mutant_preserves_dirty_source(tmp_path):
    repo = _soundness_repo(tmp_path, body='def test_value(): assert True\n')
    (repo / 'victim.py').write_text('LOCAL = 99\n')
    with mutate.IsolatedSweep.create(repo, 'HEAD', state_root=tmp_path / 'state') as sweep:
        result = mutate.run_checked_mutation(sweep, 'victim.py', '1', '2', ['test_probe.py'],
            environment={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
            timeout=60, evidence_dir=tmp_path / 'evidence')
        assert result.signal == 'TESTS_PASSED', result.detail
        assert result.status == 'UNVERIFIED' and result.exit_code == 1
    assert (repo / 'victim.py').read_text() == 'LOCAL = 99\n'


def test_provenance_environment_pins_root_without_mutating_input(tmp_path):
    env = {"PYTHONPATH": "relative-path" + os.pathsep + str(tmp_path), "KEEP": "yes"}
    before = env.copy()
    prepared = mutate.provenance_environment(tmp_path, env)
    assert env == before
    assert prepared["PYTHONPATH"].split(os.pathsep) == [str(tmp_path.resolve()), str(pathlib.Path('relative-path').resolve())]
    assert prepared["KEEP"] == "yes"


@DARWIN_ONLY
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
@DARWIN_ONLY
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


@DARWIN_ONLY
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
def test_diagnostic_labels_reject_normal_assignment(tmp_path, returncode):
    import dataclasses
    signal = 'TESTS_PASSED' if returncode == 0 else 'TEST_FAILURE'
    result = mutate.CheckedResult(signal, 'diagnostic', tmp_path / 'evidence', 'digest')
    assert result.status == 'UNVERIFIED' and result.authoritative is False
    assert result.exit_code == 1
    with pytest.raises((TypeError, ValueError)):
        dataclasses.replace(result, status='SURVIVED')
    with pytest.raises((TypeError, AttributeError)):
        result.status = 'caught'
    assert dataclasses.asdict(result)['status'] == 'UNVERIFIED'
    assert not hasattr(result, '__dict__')
    with pytest.raises(TypeError):
        mutate.CheckedResult(signal, 'diagnostic', tmp_path / 'evidence', 'digest', status='caught')


@DARWIN_ONLY
def test_phase_result_rejects_normal_authority_assignment(tmp_path):
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
def test_terminal_cannot_promote_diagnostic_results(tmp_path, capsys, legacy_label):
    result = mutate.CheckedResult(legacy_label, 'diagnostic', tmp_path / 'unused', 'digest')
    with pytest.raises(mutate.IsolationError, match='unsupported diagnostic signal'):
        mutate.present_checked_result(result)
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('observed_exit', [0, 1])
@DARWIN_ONLY
def test_real_cli_observations_never_satisfy_authoritative_gate(tmp_path, observed_exit):
    body = """from pathlib import Path
import cps
from victim import VALUE
def test_value():
    assert Path(cps.__file__).resolve().parent == Path.cwd() / 'cps'
    assert Path('collateral.py').read_text() == 'ORIGINAL\\n'
    assert not Path('phase.ignored').exists()
    Path('collateral.py').write_text('phase dirt')
    Path('phase.ignored').touch()
    assert """ + ('VALUE in (1, 2)' if observed_exit == 0 else 'VALUE == 1') + '\n'
    repo = _soundness_repo(tmp_path, body=body)
    seed = _git(repo, 'rev-parse', 'HEAD')
    # Committed state only: this local change must neither be graded nor reset.
    (repo / 'victim.py').write_text('LOCAL = 99\n')
    before = _git(repo, 'status', '--porcelain')
    evidence = tmp_path / 'evidence'
    result = subprocess.run(
        [sys.executable, str(_HARNESS), '--repo', str(repo), '--seed', seed,
         '--evidence-dir', str(evidence), '--file', 'victim.py', '--old', 'VALUE = 1',
         '--new', 'VALUE = 2', '--test', 'test_probe.py'],
        cwd=repo, capture_output=True, text=True, timeout=120,
        env={**os.environ, 'PYTEST_ADDOPTS': '', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
    )
    assert result.returncode == 1, result.stderr
    assert 'UNVERIFIED ' + ('TESTS_PASSED' if observed_exit == 0 else 'TEST_FAILURE') in result.stdout, result.stdout
    assert 'caught' not in result.stdout and 'SURVIVED' not in result.stdout
    payloads = list(evidence.glob('*.json'))
    assert len(payloads) == 1
    payload = json.loads(payloads[0].read_text())
    assert [p['returncode'] for p in payload['phases']] == [0, 0, observed_exit]
    assert payload['status'] == 'UNVERIFIED'
    assert (repo / 'victim.py').read_text() == 'LOCAL = 99\n'
    assert _git(repo, 'status', '--porcelain') == before
    assert len(_git(repo, 'worktree', 'list', '--porcelain').split('worktree ')) == 2
    print(result.stdout.replace(seed, '<pinned-seed>'), end='')
    print(f'CLI isolated phases=3 observed_exit={observed_exit} terminal_exit=1 source-preserved (Mac/APFS only)')


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


@DARWIN_ONLY
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
    if body and 'victim' not in body:
        body = 'import victim\n' + body
    (repo / 'test_probe.py').write_text(body or 'from victim import VALUE\ndef test_value(): assert VALUE == 1\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'soundness fixture')
    return repo


@DARWIN_ONLY
def test_execution_real_broken_baseline_stops_mutant(tmp_path):
    repo = _soundness_repo(tmp_path, broken=True)
    trace = []
    with mutate.IsolatedSweep.create(repo, 'HEAD', state_root=tmp_path / 'state') as sweep:
        with pytest.raises(mutate.IsolationError):
            mutate._assess_mutation(sweep, 'victim.py', '2', '1', ['test_probe.py'],
                {**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}, 10, trace)
        assert [name for name, _, _ in trace] == ['collection', 'baseline']


@DARWIN_ONLY
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


@DARWIN_ONLY
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
@DARWIN_ONLY
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


def test_git_writing_policy_names_shared_state_and_demonstrates_limit(tmp_path):
    repo, seed = _committed_repo(tmp_path)
    # Only this fixture's independent repository is changed.
    with mutate.IsolatedSweep.create(repo, seed, state_root=tmp_path / 'state') as sweep:
        _git(sweep.root, 'update-ref', 'refs/heads/policy-probe', seed)
        _git(sweep.root, 'config', 'mutation.policyProbe', 'changed')
        sweep.scrub()
        assert _git(repo, 'rev-parse', 'refs/heads/policy-probe') == seed
        assert _git(repo, 'config', 'mutation.policyProbe') == 'changed'
    print('POLICY shared refs/common config survive scrub: UNSUPPORTED (Mac/APFS only)')


@pytest.mark.parametrize('content', ['{}', 'not json'])
def test_cli_refuses_legacy_journal_without_changing_it(tmp_path, monkeypatch, capsys, content):
    monkeypatch.setattr(mutate, 'REPO', tmp_path / 'source')
    monkeypatch.setattr(mutate.tempfile, 'gettempdir', lambda: str(tmp_path))
    mutate.REPO.mkdir()
    journal = mutate.legacy_journal(mutate.REPO)
    journal.parent.mkdir(parents=True)
    journal.write_text(content)
    monkeypatch.setattr(sys, 'argv', ['mutate.py', '--seed', 'HEAD', '--file', 'unused', '--old', 'a',
                                      '--new', 'b', '--test', 'unused'])
    assert mutate.main() == 1
    output = capsys.readouterr().out
    assert 'legacy journal detected' in output and 'preserve' in output
    assert journal.read_text() == content


def test_cli_has_no_clear_journal_option(monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['mutate.py', '--seed', 'HEAD', '--clear-journal'])
    with pytest.raises(SystemExit) as exc:
        mutate.main()
    assert exc.value.code == 2


@pytest.mark.parametrize('mode', ['spec', 'provenance_reject'])
@DARWIN_ONLY
def test_cli_spec_and_provenance_rejection_are_live(tmp_path, mode):
    repo = _soundness_repo(tmp_path, body='def test_value(): assert True\n')
    if mode == 'provenance_reject':
        (repo / 'conftest.py').write_text("import cps\nfrom pathlib import Path\ncps.__file__ = str(Path(__file__).resolve().parents[1] / 'metadata.json')\n")
        _git(repo, 'add', '.')
        _git(repo, 'commit', '-qm', 'outside provenance fixture')
    spec = tmp_path / 'spec.json'
    spec.write_text(json.dumps([
        {'file': 'victim.py', 'old': '1', 'new': str(value), 'test': ['test_probe.py']}
        for value in (2, 3)]))
    evidence = tmp_path / 'evidence'
    result = subprocess.run([sys.executable, str(_HARNESS), '--repo', str(repo),
        '--seed', 'HEAD', '--spec', str(spec), '--evidence-dir', str(evidence)],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, 'PYTEST_ADDOPTS': '', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'})
    assert result.returncode == 1
    payloads = [json.loads(p.read_text()) for p in evidence.glob('*.json')]
    if mode == 'spec':
        assert len(payloads) == 2
        assert all(p['signal'] == 'TESTS_PASSED' and len(p['phases']) == 3 for p in payloads)
        assert len({p['seed_sha'] for p in payloads}) == 1
    else:
        assert len(payloads) == 1
        assert payloads[0]['signal'] == 'ERROR'
        assert 'provenance REJECTED: pytest resolved outside disposable root' in result.stdout
        assert payloads[0]['phases'] == []
    assert (repo / 'victim.py').read_text() == 'VALUE = 1\n'
    print(f'CLI {mode}: UNVERIFIED exit=1 evidence-count={len(payloads)} (Mac/APFS only)')


@pytest.mark.parametrize('defect', ['empty', 'malformed', 'traversal', 'option'])
def test_cli_invalid_spec_never_allocates_sweep(tmp_path, monkeypatch, capsys, defect):
    repo, _ = _committed_repo(tmp_path)
    spec = tmp_path / 'spec.json'
    item = {'file': 'victim.py', 'old': '1', 'new': '2', 'test': ['test_probe.py']}
    if defect == 'traversal': item['file'] = '../victim.py'
    if defect == 'option': item['test'] = ['--pyargs']
    spec.write_text('not json' if defect == 'malformed' else json.dumps([] if defect == 'empty' else [item]))
    monkeypatch.setattr(sys, 'argv', ['mutate.py', '--repo', str(repo), '--seed', 'HEAD', '--spec', str(spec)])
    def forbidden(*a, **k): pytest.fail('invalid specification allocated a sweep')
    monkeypatch.setattr(mutate.IsolatedSweep, 'create', forbidden)
    assert mutate.main() == 1
    assert 'UNVERIFIED ERROR' in capsys.readouterr().out


@DARWIN_ONLY
def test_review_preflight_cannot_erase_measured_mutant(tmp_path):
    repo = _soundness_repo(tmp_path)
    (repo / 'conftest.py').write_text(
        'import os\nfrom pathlib import Path\n'
        'if os.environ.get("CWNG_PROVENANCE_ROOT"):\n'
        '    Path("victim.py").write_text("VALUE = 1\\n")\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'preflight erasure reproduction')
    (repo / 'victim.py').write_text('VALUE = 2\n')
    direct = subprocess.run([sys.executable, '-m', 'pytest', 'test_probe.py', '-q',
        '-o', 'addopts=', '-p', 'no:cacheprovider', '--color=no'], cwd=repo,
        env={**os.environ, 'PYTHONPATH': str(repo), 'PYTEST_ADDOPTS': '',
             'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}, capture_output=True, text=True, timeout=60)
    _git(repo, 'restore', 'victim.py')
    evidence = tmp_path / 'evidence'
    cli = subprocess.run([sys.executable, str(_HARNESS), '--repo', str(repo), '--seed', 'HEAD',
        '--evidence-dir', str(evidence), '--file', 'victim.py', '--old', 'VALUE = 1',
        '--new', 'VALUE = 2', '--test', 'test_probe.py'], capture_output=True, text=True,
        env={**os.environ, 'PYTEST_ADDOPTS': '', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}, timeout=120)
    payload = json.loads(next(evidence.glob('*.json')).read_text())
    codes = [p['returncode'] for p in payload['phases']]
    print(f'REVIEW1 DIRECT_EXIT={direct.returncode} HARNESS_EXIT={cli.returncode} SIGNAL={payload["signal"]} PHASE_RETURNCODES={codes} (Mac/APFS only)')
    assert direct.returncode == 1
    assert payload['signal'] == 'TEST_FAILURE' and codes == [0, 0, 1], cli.stdout


@pytest.mark.parametrize('fault', ['scrub', 'verification'])
@DARWIN_ONLY
def test_review_post_preflight_boundary_rejects_contamination(tmp_path, monkeypatch, fault):
    repo, seed = _committed_repo(tmp_path)
    with mutate.IsolatedSweep.create(repo, seed, state_root=tmp_path / 'state') as sweep:
        plan = mutate.prepare_mutation(sweep.root, 'victim.py', '1', '2')
        def poison(*a, **k):
            if fault == 'scrub': (sweep.root / 'collateral.py').write_text('poison')
        monkeypatch.setattr(mutate, 'provenance_preflight', poison)
        if fault == 'verification':
            original = mutate.apply_mutation
            def bad_apply(root, plan):
                original(root, plan)
                (root / plan.relative).write_bytes(plan.before)
            monkeypatch.setattr(mutate, 'apply_mutation', bad_apply)
        def measured(*a, **k):
            assert (sweep.root / 'collateral.py').read_text() == 'ORIGINAL\n'
            assert (sweep.root / 'victim.py').read_bytes() == plan.after
            return mutate.PhaseResult((), 0, '', '', False, None, ())
        monkeypatch.setattr(mutate, 'run_phase_process', measured)
        if fault == 'verification':
            with pytest.raises(mutate.IsolationError, match='measured'):
                sweep.run_phase(['unused'], environment={}, timeout=60,
                    ownership_contract='inherited-token', mutation=plan)
        else:
            sweep.run_phase(['unused'], environment={}, timeout=60,
                ownership_contract='inherited-token', mutation=plan)


@DARWIN_ONLY
def test_review_actual_target_import_must_be_local(tmp_path):
    body = (
        'import os,sys\nfrom pathlib import Path\nimport cps\n'
        'sys.path.insert(0, os.environ["SOURCE_CHECKOUT"])\nimport victim\n'
        'Path(os.environ["IMPORT_WITNESS"]).write_text("SOURCE_CHECKOUT" if '
        'Path(victim.__file__).resolve() == Path(os.environ["SOURCE_CHECKOUT"]) / "victim.py" else "OTHER")\n'
        'def test_value(): assert victim.VALUE == 1\n')
    repo = _soundness_repo(tmp_path, body=body)
    witness = tmp_path / 'import-witness'
    evidence = tmp_path / 'evidence'
    cli = subprocess.run([sys.executable, str(_HARNESS), '--repo', str(repo), '--seed', 'HEAD',
        '--evidence-dir', str(evidence), '--file', 'victim.py', '--old', 'VALUE = 1',
        '--new', 'VALUE = 2', '--test', 'test_probe.py'], capture_output=True, text=True,
        env={**os.environ, 'SOURCE_CHECKOUT': str(repo), 'IMPORT_WITNESS': str(witness),
             'PYTEST_ADDOPTS': '', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}, timeout=120)
    payload = json.loads(next(evidence.glob('*.json')).read_text())
    print(f'REVIEW2 IMPORTED_FROM={witness.read_text()} SIGNAL={payload["signal"]} HARNESS_EXIT={cli.returncode} (Mac/APFS only)')
    assert payload['signal'] == 'ERROR' and 'target provenance' in payload['detail'], cli.stdout


@DARWIN_ONLY
def test_review_unobserved_target_is_not_a_passing_mutation(tmp_path):
    repo = _soundness_repo(tmp_path)
    (repo / 'test_probe.py').write_text('def test_value(): assert True\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'unobserved target')
    with mutate.IsolatedSweep.create(repo, 'HEAD', state_root=tmp_path / 'state') as sweep:
        result = mutate.run_checked_mutation(sweep, 'victim.py', '1', '2', ['test_probe.py'],
            environment={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}, timeout=60,
            evidence_dir=tmp_path / 'evidence')
    assert result.signal == 'ERROR' and 'target provenance' in result.detail


@pytest.mark.parametrize('relative', ['victim.py', 'cps/__init__.py'])
@DARWIN_ONLY
def test_review_target_witness_accepts_real_local_execution(tmp_path, relative):
    repo = _soundness_repo(tmp_path)
    if relative == 'cps/__init__.py':
        (repo / relative).write_text('VALUE = 1\n')
        body = 'def test_value():\n    import cps\n    assert cps.VALUE == 1\n'
    else:
        body = 'def test_value():\n    import victim\n    assert victim.VALUE == 1\n'
    (repo / 'test_probe.py').write_text(body)
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'local target witness')
    with mutate.IsolatedSweep.create(repo, 'HEAD', state_root=tmp_path / 'state') as sweep:
        result = mutate.run_checked_mutation(sweep, relative, '1', '2', ['test_probe.py'],
            environment={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}, timeout=60,
            evidence_dir=tmp_path / 'evidence')
    assert result.signal == 'TEST_FAILURE', result.detail


@pytest.mark.parametrize('shape', ['setattr', 'duck'])
def test_review_forged_authority_cannot_exit_zero(tmp_path, shape):
    from types import SimpleNamespace
    evidence = tmp_path / 'api-evidence.json'
    evidence.write_text('{}')
    result = mutate.CheckedResult('TESTS_PASSED', 'forged public-API result',
                                  evidence, mutate._digest(evidence))
    if shape == 'setattr':
        object.__setattr__(result, 'status', 'SURVIVED')
        object.__setattr__(result, 'authoritative', True)
        object.__setattr__(result, 'exit_code', 0)
    else:
        result = SimpleNamespace(signal='TESTS_PASSED', detail='forged',
            evidence=evidence, evidence_sha256=mutate._digest(evidence),
            status='SURVIVED', authoritative=True, exit_code=0)
    try:
        code = mutate.present_checked_result(result)
        print(f'REVIEW3 shape={shape} STATUS={result.status} AUTHORITATIVE={result.authoritative} EXIT_CODE={result.exit_code} PRESENT_RETURN={code} (Mac/APFS only)')
    except mutate.IsolationError:
        print(f'REVIEW3 shape={shape} PRESENT=REJECTED (Mac/APFS only)')
        return
    pytest.fail(f'forged result reached presentation with exit {code}')


def test_review_matrix_driver_is_diagnostic_and_nonzero(tmp_path):
    output = tmp_path / 'check-output.txt'
    proc = subprocess.run([sys.executable, str(_HARNESS.with_name('check_mutations.py')),
        '--only', 'equivalent_integer_bound', '--output', str(output)],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, 'PYTEST_ADDOPTS': ''})
    print(proc.stdout, end='')
    print(f'REVIEW4 EXIT={proc.returncode} (Mac/APFS only)')
    assert proc.returncode == 1
    assert 'UNVERIFIED' in proc.stdout and 'UNVERIFIED' in output.read_text()
    assert 'SURVIVOR' not in proc.stdout


def test_platform_partition_retains_native_coverage(request):
    marked = [item for item in request.session.items
              if any(mark.kwargs.get('reason') == DARWIN_REASON for mark in item.iter_markers('skipif'))]
    assert marked, 'macOS-specific tests must remain visibly collected'
    for item in marked:
        mark = next(m for m in item.iter_markers('skipif') if m.kwargs.get('reason') == DARWIN_REASON)
        assert mark.args[0] is (TEST_PLATFORM != 'darwin')


@pytest.mark.parametrize('operation', ['create', 'close', 'failed_add'])
def test_cleanup_preserves_unrelated_absent_worktree(tmp_path, monkeypatch, operation):
    repo, seed = _committed_repo(tmp_path)
    state = tmp_path / 'state'
    sweep = mutate.IsolatedSweep.create(repo, seed, state_root=state) if operation == 'close' else None
    other = tmp_path / 'other'
    parked = tmp_path / 'parked'
    _git(repo, 'worktree', 'add', '--detach', str(other), seed)
    other.rename(parked)
    original_git = mutate._git
    if operation == 'failed_add':
        def fail_add(repo, *args):
            if args[:2] == ('worktree', 'add'):
                raise mutate.IsolationError('injected add failure')
            return original_git(repo, *args)
        monkeypatch.setattr(mutate, '_git', fail_add)
        # Exercise the add-failure cleanup separately from the stale reaper.
        monkeypatch.setattr(mutate, '_reap_stale_sweeps', lambda *args: [])
    try:
        if operation == 'failed_add':
            with pytest.raises(mutate.IsolationError, match='injected'):
                mutate.IsolatedSweep.create(repo, seed, state_root=state)
        elif operation == 'create':
            sweep = mutate.IsolatedSweep.create(repo, seed, state_root=state)
        else:
            sweep.close()
        assert f'worktree {other}' in _git(repo, 'worktree', 'list', '--porcelain')
    finally:
        parked.rename(other)
        if sweep is not None:
            sweep.close()


def test_git_timeout_is_a_handled_isolation_error(tmp_path, monkeypatch, capsys):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(['git'], 120)
    monkeypatch.setattr(mutate.subprocess, 'run', timeout)
    with pytest.raises(mutate.IsolationError, match='git command timed out'):
        mutate._git(tmp_path, 'rev-parse', 'HEAD')
    monkeypatch.setattr(sys, 'argv', ['mutate.py', '--repo', str(tmp_path),
        '--seed', 'HEAD', '--file', 'victim.py', '--old', '1', '--new', '2',
        '--test', 'test_victim.py'])
    assert mutate.main() == 1
    assert 'UNVERIFIED ERROR: git command timed out' in capsys.readouterr().out


@DARWIN_ONLY
def test_group_signal_handles_confirmed_zombie_only_eperm(tmp_path):
    child = subprocess.Popen([sys.executable, '-c', 'pass'], start_new_session=True)
    try:
        deadline = time.monotonic() + 10
        while True:
            state = subprocess.check_output(['ps', '-p', str(child.pid), '-o', 'state='], text=True).strip()
            if state.startswith('Z'):
                break
            assert time.monotonic() < deadline, 'child did not exit before watchdog'
            time.sleep(.01)
        # Popen has not reaped this child; Darwin rejects its zombie-only group.
        mutate._signal_group(child.pid, signal.SIGKILL)
    finally:
        child.wait(timeout=10)
    assert child.returncode == 0


@pytest.mark.parametrize('rows,code', [
    ('123 501 S\n', 0), ('123 501 Z\n123 501 S\n', 0),
    ('123 502 Z\n', 0), ('', 0), ('malformed\n', 0), ('123 501 Z\n', 1),
])
def test_group_signal_does_not_hide_unconfirmed_permission_errors(monkeypatch, rows, code):
    def denied(*args):
        raise PermissionError(1, 'injected permission error')
    monkeypatch.setattr(mutate.os, 'killpg', denied)
    monkeypatch.setattr(mutate.os, 'getuid', lambda: 501)
    monkeypatch.setattr(mutate.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess(a, code, rows, ''))
    with pytest.raises((PermissionError, mutate.IsolationError)):
        mutate._signal_group(123, signal.SIGKILL)


@pytest.mark.parametrize('pgid', [0, -1, -123])
def test_group_signal_refuses_nonpositive_ids_before_kernel(monkeypatch, pgid):
    calls = []
    monkeypatch.setattr(mutate.os, 'killpg', lambda *args: calls.append(args))
    with pytest.raises(mutate.IsolationError, match='positive process group'):
        mutate._signal_group(pgid, signal.SIGKILL)
    assert calls == []


@pytest.fixture
def container_backend(monkeypatch):
    import shutil
    if shutil.which('docker') is None:
        pytest.skip('Docker required for real Linux container boundary tests')
    module_path = pathlib.Path(__file__).resolve().parents[1] / 'mutation/container_backend.py'
    spec = importlib.util.spec_from_file_location('container_backend', module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    # Explicit negative-control mode. The fixture owns an emergency cleanup
    # for these deliberate defects, after test assertions have seen the leak.
    fault = os.environ.get('CWNG_CONTAINER_TEST_FAULT')
    if not fault:
        yield module
        return
    original = module._docker
    owned = []

    def defective(*args, **kwargs):
        if fault == 'cleanup' and args[0] == 'rm':
            return subprocess.CompletedProcess(args, 0, b'', b'')
        if fault == 'cleanup' and args[:2] == ('ps', '-aq'):
            return subprocess.CompletedProcess(args, 0, b'', b'')
        result = original(*args, **kwargs)
        if args[0] == 'create' and result.returncode == 0:
            owned.append(result.stdout.decode().strip())
        return result

    monkeypatch.setattr(module, '_docker', defective)
    if fault == 'seed':
        original_init = module.ContainerSweep.__init__

        def changed_seed(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            self.archive = self.archive.replace(b'VALUE = 1', b'VALUE = 9')

        monkeypatch.setattr(module.ContainerSweep, '__init__', changed_seed)
    try:
        yield module
    finally:
        for cid in owned:
            if original('container', 'inspect', cid).returncode == 0:
                assert original('rm', '-f', cid).returncode == 0


@pytest.mark.parametrize('finish', ['exit', 'timeout', 'failure'])
def test_container_tokenless_writes_stop_after_phase(tmp_path, container_backend, finish):
    repo, seed = _committed_repo(tmp_path)
    out = tmp_path / 'out'
    out.mkdir()
    sweep = container_backend.ContainerSweep(repo, seed)
    # Same setsid/env -i counterexample as the diagnostic limitation. Bound the
    # writer too, so an intentionally red cleanup regression cannot live forever.
    writer = "i=0; while [ $i -lt 80 ]; do echo tick >> /out/escaped.log; i=$((i+1)); sleep 0.1; done"
    tail = {'exit': 'exit 0', 'failure': 'exit 7', 'timeout': 'sleep 20'}[finish]
    result = sweep.run_phase(['sh', '-c',
        "setsid env -i sh -c '" + writer + "' >/dev/null 2>&1 & "
        "while [ ! -s /out/escaped.log ]; do sleep 0.1; done; sleep 0.3; " + tail],
        output=out, timeout=2 if finish == 'timeout' else 20)
    before = (out / 'escaped.log').read_bytes()
    time.sleep(1)
    after = (out / 'escaped.log').read_bytes()
    assert before.count(b'tick') >= 2
    assert after == before, 'tokenless detached writer survived container removal'
    assert result.timed_out is (finish == 'timeout')
    assert result.returncode == {'exit': 0, 'failure': 7, 'timeout': None}[finish]
    remaining = subprocess.run(['docker', 'ps', '-aq', '--filter',
        'id=' + result.container_id], check=True, capture_output=True, text=True).stdout.strip()
    assert remaining == ''
    print(f'CONTAINMENT {finish}: alive_writes={before.count(b"tick")} '
          f'after_removal_writes={after.count(b"tick") - before.count(b"tick")} containers=0')


def test_container_phases_copy_pinned_seed_and_discard_damage(tmp_path, container_backend):
    repo, seed = _committed_repo(tmp_path)
    sweep = container_backend.ContainerSweep(repo, seed)
    (repo / 'victim.py').write_text('DIRTY SOURCE\n')
    ids = []
    for index in range(2):
        out = tmp_path / f'out{index}'
        out.mkdir()
        result = sweep.run_phase(['python', '-c',
            "from pathlib import Path; "
            "assert Path('victim.py').read_text() == 'VALUE = 1\\n'; "
            "assert not Path('.git').exists(); "
            "Path('victim.py').write_text('DAMAGE'); print('pinned seed')"], output=out)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'pinned seed'
        assert result.seed_sha == seed
        ids.append(result.container_id)
    assert len(set(ids)) == 2
    assert (repo / 'victim.py').read_text() == 'DIRTY SOURCE\n'


def test_container_setup_failure_removes_owned_container(tmp_path, container_backend, monkeypatch):
    repo, seed = _committed_repo(tmp_path)
    sweep = container_backend.ContainerSweep(repo, seed)
    out = tmp_path / 'out'
    out.mkdir()
    original = container_backend._docker
    owned = []

    def inject(*args, **kwargs):
        if args[0] == 'cp':
            raise KeyboardInterrupt('injected after create')
        result = original(*args, **kwargs)
        if args[0] == 'create' and result.returncode == 0:
            owned.append(result.stdout.decode().strip())
        return result

    monkeypatch.setattr(container_backend, '_docker', inject)
    with pytest.raises(KeyboardInterrupt):
        sweep.run_phase(['true'], output=out)
    assert len(owned) == 1
    assert subprocess.run(['docker', 'ps', '-aq', '--filter', 'id=' + owned[0]],
        check=True, capture_output=True).stdout.strip() == b''
    with pytest.raises(RuntimeError, match='after an error'):
        sweep.run_phase(['true'], output=out)


@pytest.mark.parametrize('mode', ['clean_control', 'absent_control', 'startup_rewrite',
                                 'frame_forge', 'meta_transform', 'loader_transform'])
def test_container_leg7_execution_provenance_limit(tmp_path, container_backend, mode):
    import hashlib
    directory = pathlib.Path(__file__).resolve().parents[1] / 'mutation'
    modules = []
    for name in ('leg7_probe', 'container_probe'):
        spec = importlib.util.spec_from_file_location(name, directory / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    leg7, probe = modules
    repo = leg7.fixture(tmp_path, mode)
    seed = _git(repo, 'rev-parse', 'HEAD')
    out = tmp_path / 'out'
    out.mkdir()
    sweep = container_backend.ContainerSweep(repo, seed)
    result = sweep.run_phase(probe.COMMAND, output=out, files=probe.runtime_overlay(),
                             environment=probe.ENVIRONMENT, timeout=30)
    assert (out / 'report.json').exists(), result.stderr
    report = json.loads((out / 'report.json').read_text())
    witness = report['target_provenance']
    assert report['complete'] is True
    assert report['selected_count'] == 1
    assert witness['active'] is True and witness['foreign'] is False
    assert result.returncode == (1 if mode == 'clean_control' else 0), result.stderr
    assert witness['seen'] is (mode != 'absent_control')
    diagnostic = mutate.PhaseResult(tuple(probe.COMMAND), result.returncode, result.stdout,
        result.stderr, result.timed_out, None, ())
    check = mutate.validate_execution(diagnostic, report, ('test_probe.py::test_value',))
    assert check.signal == ('TEST_FAILURE' if mode == 'clean_control' else 'TESTS_PASSED')
    assert result.status == 'UNVERIFIED' and result.authoritative is False
    if mode in ('meta_transform', 'loader_transform'):
        assert (out / 'loaded-hash').read_text() == hashlib.sha256(b'VALUE = 2\n').hexdigest()
    print(f'LINUX PROBE {mode}: exit={result.returncode} seen={witness["seen"]} '
          f'active={witness["active"]} signal={check.signal} authority={result.authoritative}')
    # Red requirement run: a passing mutant must not obtain an accepted witness
    # when these vectors replace its execution. Keep the failed requirement
    # reproducible; the normal test asserts the observed limitation honestly.
    if os.environ.get('CWNG_REQUIRE_CONTAINER_PROVENANCE') == '1' and mode not in (
            'clean_control', 'absent_control'):
        assert not witness['seen'] or result.returncode != 0, (
            'container removal did not detect substituted target execution')


def test_container_output_names_external_delegation_limits(container_backend, capsys):
    result = container_backend.ContainerObservation(0, 'untrusted output', '', False, 'id', 'seed')
    assert container_backend.present_observation(result) == 1
    text = capsys.readouterr().out
    for term in ('UNVERIFIED', 'databases', 'network services', 'shared ports',
                 'remote service managers', 'daemons outside', 'Not hermetic'):
        assert term in text
    assert 'untrusted output' not in text
    assert 'SURVIVED' not in text and 'caught' not in text
    print(text, end='')


@pytest.mark.parametrize('shape', ['setattr', 'subclass', 'duck'])
def test_container_presentation_rejects_forged_authority(container_backend, shape):
    from types import SimpleNamespace
    result_type = container_backend.ContainerObservation
    if shape == 'subclass':
        class Derived(result_type):
            pass
        result_type = Derived
    result = result_type(0, '', '', False, 'id', 'seed')
    if shape == 'setattr':
        object.__setattr__(result, 'authoritative', True)
        object.__setattr__(result, 'status', 'SURVIVED')
    if shape == 'duck':
        result = SimpleNamespace(status='UNVERIFIED', authoritative=False)
    with pytest.raises(ValueError, match='invalid'):
        container_backend.present_observation(result)


@pytest.mark.parametrize('kind', ['caught', 'survived', 'baseline_error', 'spec'])
def test_container_cli_runs_real_sweep(tmp_path, container_backend, kind):
    repo, seed = _committed_repo(tmp_path)
    assertion = {'caught': 'victim.VALUE == 1', 'survived': 'victim.VALUE > 0',
                 'baseline_error': 'False', 'spec': 'victim.VALUE == 1'}[kind]
    (repo / 'test_probe.py').write_text(
        "from pathlib import Path\nimport victim\n"
        "assert not Path('previous-phase').exists()\n"
        "Path('previous-phase').write_text('seen')\n"
        f"def test_value(): assert {assertion}\n")
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-qm', 'container CLI fixture')
    seed = _git(repo, 'rev-parse', 'HEAD')
    evidence = tmp_path / 'evidence'
    command = [sys.executable, str(_HARNESS), '--backend', 'container', '--repo', str(repo),
               '--seed', seed, '--evidence-dir', str(evidence), '--scratch-dir', str(tmp_path)]
    if kind == 'spec':
        spec = tmp_path / 'spec.json'
        spec.write_text(json.dumps([{'file': 'victim.py', 'old': '1', 'new': str(n),
                                    'test': 'test_probe.py'} for n in (2, 3)]))
        command += ['--spec', str(spec)]
    else:
        command += ['--file', 'victim.py', '--old', '1', '--new', '2', '--test', 'test_probe.py']
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert result.returncode == {'caught': 0, 'survived': 1, 'baseline_error': 2, 'spec': 0}[kind], result.stdout + result.stderr
    reports = [json.loads(path.read_text()) for path in evidence.glob('*.json')]
    assert len(reports) == (2 if kind == 'spec' else 1)
    expected = 'ERROR' if kind == 'baseline_error' else ('SURVIVED' if kind == 'survived' else 'caught')
    assert all(report['status'] == expected for report in reports)
    assert all([phase['phase'] for phase in report['phases']] ==
               (['collection', 'baseline'] if kind == 'baseline_error' else ['collection', 'baseline', 'mutant'])
               for report in reports)
    assert all(report['seed_sha'] == seed for report in reports)
    assert 'UNVERIFIED' not in result.stdout
    assert 'Traceback' not in result.stderr
    assert (repo / 'victim.py').read_text() == 'VALUE = 1\n'
    assert not (repo / 'previous-phase').exists()
    print(result.stdout, end='')


@pytest.mark.parametrize('failure,expected', [
    ('missing', 'Docker CLI not found. Install Docker'),
    ('daemon', 'Cannot reach the Docker daemon. Start Docker'),
    ('image', 'not available locally. Pull or build it first'),
    ('create', 'Docker could not create the phase container. Check free resources'),
])
def test_container_cli_docker_errors_are_actionable(tmp_path, failure, expected):
    import shutil
    repo, seed = _committed_repo(tmp_path)
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    (binaries / 'git').symlink_to(shutil.which('git'))
    if failure != 'missing':
        docker = binaries / 'docker'
        docker.write_text('#!/bin/sh\n'
            + ('exit 1\n' if failure == 'daemon' else
               'case "$1" in\ninfo) printf "linux\\n";;\n'
               + ('image) exit 1;;\n' if failure == 'image' else 'image) printf "sha256:fixture\\n";;\n')
               + '*) exit 1;;\nesac\n'))
        docker.chmod(0o755)
    result = subprocess.run([sys.executable, str(_HARNESS), '--backend', 'container',
        '--repo', str(repo), '--seed', seed, '--file', 'victim.py', '--old', '1', '--new', '2',
        '--test', 'test_probe.py', '--evidence-dir', str(tmp_path / 'evidence'),
        '--scratch-dir', str(tmp_path)], capture_output=True, text=True, timeout=30,
        env={**os.environ, 'PATH': str(binaries)})
    assert result.returncode == 2
    assert expected in result.stdout, result.stdout + result.stderr
    assert result.stdout.count('ERROR') == 1
    assert 'UNVERIFIED' not in result.stdout
    assert 'Traceback' not in result.stderr
    assert result.stderr == ''
    print(result.stdout, end='')


@pytest.mark.parametrize('state,returncode,stderr,finished', [
    ('Z\n', 0, '', True), ('', 1, '', True), ('S\n', 0, '', False),
    ('', 1, 'ps failed', False),
])
def test_darwin_process_exit_between_table_and_arguments(monkeypatch, state, returncode, stderr, finished):
    from types import SimpleNamespace
    monkeypatch.setattr(mutate.ctypes, 'CDLL', lambda *a, **k: SimpleNamespace(sysctl=lambda *a: -1))
    monkeypatch.setattr(mutate.ctypes, 'get_errno', lambda: 5)
    monkeypatch.setattr(mutate.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess(a, returncode, state, stderr))
    if finished:
        assert mutate._has_phase_token(123, 'test-token') is False
    else:
        with pytest.raises(mutate.IsolationError, match='errno 5'):
            mutate._has_phase_token(123, 'test-token')
