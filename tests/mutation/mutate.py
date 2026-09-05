#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run committed-state mutation diagnostics in a disposable detached worktree.

Every collection, baseline and mutant phase uses provenance preflight and scrub.
macOS results are UNVERIFIED and exit nonzero. Shared Git writes are UNSUPPORTED.
See tests/mutation/ISOLATION.md for the execution boundary and legacy recovery.

Usage:
    mutate.py --seed COMMIT --file F --old STR --new STR --test TARGET
    mutate.py --seed COMMIT --spec mutants.json
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import fcntl
import hashlib
import json
import os
import pathlib
import re
import ctypes
import errno
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

REPO = pathlib.Path(__file__).resolve().parents[2]
_ISOLATION_VERSION = 1


class IsolationError(RuntimeError):
    """The disposable execution tree could not be made trustworthy."""


@dataclass(frozen=True, slots=True)
class PhaseResult:
    argv: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    containment_error: str | None
    escaped_pids: tuple[int, ...]
    status: str = field(default="UNVERIFIED", init=False)
    authoritative: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class MutationPlan:
    relative: str
    before: bytes
    after: bytes


def _mutation_target(root: pathlib.Path, relative: str) -> pathlib.Path:
    target = (root / relative).resolve(strict=True)
    if pathlib.Path(relative).is_absolute() or not target.is_relative_to(root.resolve()) or not target.is_file():
        raise IsolationError("mutation target is outside the disposable file boundary")
    return target


def prepare_mutation(root: pathlib.Path, relative: str, old: str, new: str) -> MutationPlan:
    before = _mutation_target(root, relative).read_bytes()
    anchor = old.encode("utf-8")
    if not anchor or before.count(anchor) != 1:
        raise IsolationError("mutation anchor must match exactly once")
    after = before.replace(anchor, new.encode("utf-8"), 1)
    if after == before:
        raise IsolationError("no-op mutation refused before pytest")
    return MutationPlan(relative, before, after)


def apply_mutation(root: pathlib.Path, plan: MutationPlan) -> None:
    target = _mutation_target(root, plan.relative)
    if target.read_bytes() != plan.before:
        raise IsolationError("mutation source changed after preparation")
    if plan.after == plan.before:
        raise IsolationError("no-op mutation refused before pytest")
    target.write_bytes(plan.after)
    if target.read_bytes() != plan.after:
        raise IsolationError("mutation write did not produce the requested bytes")


# This is an explicit diagnostic contract, not arbitrary descendant containment.
_TOKEN_CONTRACT = "inherited-token"


class _BsdInfo(ctypes.Structure):
    """Darwin proc_bsdinfo, used to distinguish a PID from a later reuse."""

    _fields_ = (
        [(name, ctypes.c_uint32) for name in (
            "flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid",
            "rgid", "svuid", "svgid", "reserved",
        )]
        + [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
        + [(name, ctypes.c_uint32) for name in ("nfiles", "pgid", "jobc", "tdev", "tpgid")]
        + [("nice", ctypes.c_int32), ("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64)]
    )


def _process_identity(pid: int) -> tuple[int, int] | None:
    info = _BsdInfo()
    library = ctypes.CDLL(None, use_errno=True)
    count = library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    if count == ctypes.sizeof(info):
        return info.start_sec, info.start_usec
    error = ctypes.get_errno()
    if error == errno.ESRCH:
        return None
    raise IsolationError(f"cannot inspect identity of process {pid}: errno {error}")


def _has_phase_token(pid: int, token: str) -> bool:
    """Read Darwin's exec environment without logging arguments or environment."""
    library = ctypes.CDLL(None, use_errno=True)
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
    size = ctypes.c_size_t()
    for allocate in (False, True):
        buffer = ctypes.create_string_buffer(size.value) if allocate else None
        if library.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0):
            error = ctypes.get_errno()
            if error in (errno.ESRCH, errno.EINVAL):
                # Kernel tasks / exited processes have no inspectable exec args.
                return False
            raise IsolationError(f"cannot inspect process environment: errno {error}")
    data = buffer.raw[:size.value]
    argc = int.from_bytes(data[:4], sys.byteorder, signed=True)
    if argc < 0:
        raise IsolationError("malformed process arguments")
    try:
        offset = data.index(b"\0", 4) + 1  # executable path, then padding
        while offset < len(data) and data[offset] == 0:
            offset += 1
        for _ in range(argc):
            offset = data.index(b"\0", offset) + 1
    except ValueError as exc:
        raise IsolationError("malformed process arguments") from exc
    marker = f"CWNG_MUTATION_PHASE_TOKEN={token}".encode()
    return marker in data[offset:].split(b"\0")


def _phase_members(pgid: int, token: str) -> dict[int, tuple[int, bool]]:
    """Inspect group and visible inherited-token processes, retaining zombies.

    Clearing a token, changing credentials, and uninspectable exec environments
    are outside this diagnostic contract. This is not a complete ownership proof.
    """
    try:
        result = subprocess.run(
            ["ps", "-U", str(os.getuid()), "-o", "pid=,pgid=,state="],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IsolationError("cannot inspect phase process table") from exc
    if result.returncode:
        raise IsolationError("cannot inspect phase process table")
    members = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit():
            raise IsolationError("malformed process table")
        pid, group = int(fields[0]), int(fields[1])
        zombie = fields[2].startswith("Z")
        if group == pgid or (not zombie and _has_phase_token(pid, token)):
            members[pid] = (group, zombie)
    return members


def _signal_group(pgid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass
    # Permission and other errors must reach the caller's error result.


def _terminate_phase_processes(proc, token: str) -> tuple[tuple[int, ...], str | None]:
    """Kill and observe disappearance under the inherited-token diagnostic contract.

    The direct child is reaped by Popen; orphan descendants are reaped by the OS.
    Zombies are retained until disappearance, rather than treated as reaped.
    """
    escaped = set()
    known = {}
    errors = []
    deadline = time.monotonic() + 3
    while True:
        proc.poll()  # reap the leader before checking the process group
        try:
            members = _phase_members(proc.pid, token)
            for pid, (group, zombie) in members.items():
                identity = _process_identity(pid)
                if identity is not None:
                    known[pid] = identity
                    if group != proc.pid:
                        escaped.add(pid)
            # Kill immediately: a grace period permits further forks and writes.
            # Do not signal a reused process-group ID after the group has vanished.
            if any(group == proc.pid for group, _ in members.values()):
                _signal_group(proc.pid, signal.SIGKILL)
            for pid, identity in list(known.items()):
                current = _process_identity(pid)
                if current != identity:
                    del known[pid]
                    continue
                if pid in members and members[pid][1]:
                    continue  # a zombie cannot be signalled into being reaped
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.poll()
            if not members and not known and proc.returncode is not None:
                break
        except (IsolationError, OSError) as exc:
            errors.append(str(exc))
            # Ownership inspection failure must not bypass direct group cleanup.
            try:
                _signal_group(proc.pid, signal.SIGKILL)
            except OSError as cleanup_error:
                errors.append(str(cleanup_error))
            break
        if time.monotonic() >= deadline:
            errors.append("phase processes remain or have not been reaped before cleanup deadline")
            break
        time.sleep(.01)
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        errors.append("phase leader survived termination")
    if escaped:
        errors.append(f"phase process escaped its process group: {sorted(escaped)}")
    return tuple(sorted(escaped)), "; ".join(errors) or None


def run_phase_process(
    argv: list[str],
    *,
    cwd: pathlib.Path,
    environment: dict[str, str],
    timeout: float,
    artifacts: pathlib.Path,
    ownership_contract: str | None = None,
) -> PhaseResult:
    """Run a Mac diagnostic phase with a deliberately restricted ownership contract.

    Arbitrary process-tree containment is unavailable on this backend. The default
    rejects before launch. Diagnostic callers must explicitly require every child
    to retain its token and user identity, and permit process-table inspection.
    The command-line flow opts in only for UNVERIFIED diagnostics.
    """
    if ownership_contract != _TOKEN_CONTRACT or sys.platform != "darwin":
        raise IsolationError(
            "arbitrary descendant containment is unavailable; "
            "the Mac diagnostic backend requires an explicit inherited-token contract"
        )
    if timeout <= 0 or not argv:
        raise ValueError("a phase needs an argv and a positive timeout")
    artifacts.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    env = {**environment, "CWNG_MUTATION_PHASE_TOKEN": token}
    stdout_path = artifacts / f"{token}.stdout"
    stderr_path = artifacts / f"{token}.stderr"
    timed_out = False
    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        # No gate or tracker is needed: ownership is inherited at exec, not sampled
        # from a disappearing parent-child relationship after a fork notification.
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env, stdout=out, stderr=err, start_new_session=True,
        )
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            escaped, containment_error = _terminate_phase_processes(proc, token)
    return PhaseResult(
        tuple(argv), proc.returncode, stdout_path.read_text(errors="replace"),
        stderr_path.read_text(errors="replace"), timed_out, containment_error, escaped,
    )


def provenance_environment(root: pathlib.Path, environment: dict[str, str]) -> dict[str, str]:
    """Keep the disposable root first even for children that change cwd.

    The venv, home, temporary directories, shared Git data, services, network,
    ports, caches and escaped processes remain outside this boundary.
    """
    root = root.resolve(strict=True)
    inherited = environment.get("PYTHONPATH", "")
    # Resolve relative entries now so a different-cwd child cannot reinterpret them.
    paths = [str(root)]
    for value in inherited.split(os.pathsep):
        if value:
            path = str(pathlib.Path(value).resolve())
            if path not in paths:
                paths.append(path)
    return {**environment, "PYTHONPATH": os.pathsep.join(paths),
            "PYTHONDONTWRITEBYTECODE": "1"}


def provenance_preflight(
    root: pathlib.Path, *, environment: dict[str, str], artifacts: pathlib.Path,
    console: pathlib.Path | None = None, pytest_targets: list[str] | None = None,
) -> tuple[dict, ...]:
    """Check the supplied environment in three real invocation contexts.

    This deliberately does not repair the environment: callers prepare it with
    provenance_environment, and the preflight must reject a broken one.
    """
    root = root.resolve(strict=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    console = console or pathlib.Path(sys.executable).parent / "cps"
    if not console.is_file():
        raise IsolationError("provenance REJECTED: installed cps console script is missing")
    child_cwd = artifacts / "different-cwd"
    child_cwd.mkdir(exist_ok=True)
    empty_test = artifacts / "test_provenance_empty.py"
    empty_test.write_text("# Collection-only provenance probe.\n")
    env = {**environment, "CWNG_PROVENANCE_ROOT": str(root),
           "CWNG_PROVENANCE_CONSOLE": str(console), "PYTEST_ADDOPTS": "",
           "PYTHONWARNINGS": "ignore",
           "CWNG_PROVENANCE_TARGETS": json.dumps(pytest_targets or [str(empty_test)])}
    helper = pathlib.Path(__file__).with_name("provenance_probe.py")
    # -c preserves cwd on sys.path for the pytest and different-cwd probes.
    bootstrap = "import runpy,sys; p=sys.argv.pop(1); runpy.run_path(p,run_name='__main__')"
    records = []
    for shape in ("pytest", "child", "console"):
        result = run_phase_process(
            [sys.executable, "-c", bootstrap, str(helper), shape],
            cwd=root if shape == "pytest" else child_cwd, environment=env,
            timeout=30, artifacts=artifacts, ownership_contract=_TOKEN_CONTRACT,
        )
        lines = [line.removeprefix("CWNG_PROVENANCE ") for line in result.stdout.splitlines()
                 if line.startswith("CWNG_PROVENANCE ")]
        try:
            record = json.loads(lines[0]) if len(lines) == 1 else None
        except json.JSONDecodeError:
            record = None
        if isinstance(record, dict) and record.get("shape") == shape and record.get("inside") is False:
            raise IsolationError(f"provenance REJECTED: {shape} resolved outside disposable root")
        if (result.timed_out or result.containment_error
                or result.returncode not in ((0, 5) if shape == "pytest" else (0,))):
            raise IsolationError(f"provenance REJECTED: {shape} probe execution failed "
                                 f"(exit={result.returncode}, timeout={result.timed_out}, "
                                 f"cleanup={result.containment_error})")
        if (not isinstance(record, dict) or record.get("shape") != shape
                or record.get("inside") is not True or not isinstance(record.get("paths"), list)
                or len(record["paths"]) < 3 or not all(isinstance(p, str) for p in record["paths"])):
            raise IsolationError(f"provenance REJECTED: {shape} missing or malformed import witness")
        # Validate the relative witness as well; traversal and symlinks are refused.
        for relative in record["paths"]:
            path = pathlib.Path(relative)
            if path.is_absolute() or not (root / path).resolve(strict=True).is_relative_to(root):
                raise IsolationError(f"provenance REJECTED: {shape} path outside disposable root")
        records.append(record)
    return tuple(records)


def _check_report(phase: PhaseResult, report: dict) -> None:
    if phase.timed_out or phase.containment_error:
        raise IsolationError("pytest phase timed out or failed containment")
    if (not isinstance(report, dict) or type(report.get("version")) is not int
            or report["version"] != 1 or report.get("complete") is not True
            or report.get("exitstatus") != phase.returncode):
        raise IsolationError("pytest evidence missing, incomplete or inconsistent")
    for key in ("selected", "deselected", "collection_errors", "reports"):
        if not isinstance(report.get(key), list):
            raise IsolationError("malformed pytest evidence lists")
    for event in report["reports"]:
        if isinstance(event, dict) and event.get("when") in ("setup", "teardown") and event.get("outcome") == "failed":
            raise IsolationError("pytest setup or teardown error is not a test failure")
    if report["collection_errors"]:
        raise IsolationError("pytest collection errors are infrastructure errors")
    nodes = report["selected"]
    if (not nodes or not all(isinstance(node, str) for node in nodes)
            or len(set(nodes)) != len(nodes)):
        raise IsolationError("collection did not resolve unique real test nodes")
    if type(report.get("selected_count")) is not int or report["selected_count"] != len(nodes):
        raise IsolationError("selected node count disagrees with reported collection")


def _summary_body(stdout: str) -> str:
    bodies = []
    for line in stdout.splitlines():
        line = line.strip().strip("= ")
        match = re.fullmatch(r"(.+) in [0-9]+(?:\.[0-9]+)?s(?: \([0-9:]+\))?", line)
        if match:
            bodies.append(match[1])
    if len(bodies) != 1:
        raise IsolationError("missing or ambiguous pytest summary")
    return bodies[0]


def validate_collection(root: pathlib.Path, phase: PhaseResult, report: dict) -> tuple[str, ...]:
    _check_report(phase, report)
    if phase.returncode != 0:
        raise IsolationError("pytest collection did not succeed")
    nodes = tuple(report["selected"])
    for node in nodes:
        relative, separator, selector = node.partition("::")
        path = (root / relative).resolve()
        if (not separator or not selector or pathlib.Path(relative).is_absolute()
                or not path.is_relative_to(root.resolve()) or not path.is_file()):
            raise IsolationError("collection contains a non-real or outside test node")
    match = re.fullmatch(r"([0-9]+)(?:/([0-9]+))? tests? collected(?: \(([0-9]+) deselected\))?",
                         _summary_body(phase.stdout))
    if not match:
        raise IsolationError("malformed pytest collection summary")
    selected = int(match[1])
    total = int(match[2]) if match[2] is not None else selected
    deselected = int(match[3] or 0)
    if selected != len(nodes):
        raise IsolationError("collection numerator disagrees with selected nodes")
    if total != selected + len(report["deselected"]) or deselected != len(report["deselected"]):
        raise IsolationError("collection denominator or deselection count disagrees")
    return nodes


@dataclass(frozen=True, slots=True)
class ExecutionCheck:
    signal: str
    executed: int
    failures: int
    status: str = field(default="UNVERIFIED", init=False)
    authoritative: bool = field(default=False, init=False)


def _execution_summary(stdout: str) -> dict[str, int]:
    counts = {}
    for part in _summary_body(stdout).split(", "):
        match = re.fullmatch(r"([0-9]+) (passed|failed|skipped|xfailed|xpassed|deselected|errors?|warnings?)", part)
        if not match:
            raise IsolationError("malformed pytest execution summary")
        key = {"errors": "error", "warnings": "warning"}.get(match[2], match[2])
        if key in counts:
            raise IsolationError("duplicate summary category")
        counts[key] = int(match[1])
    return counts


def validate_execution(phase: PhaseResult, report: dict, expected: tuple[str, ...], *, baseline=False) -> ExecutionCheck:
    _check_report(phase, report)
    if phase.returncode not in (0, 1):
        raise IsolationError("pytest infrastructure exit is not an execution outcome")
    if set(report["selected"]) != set(expected):
        raise IsolationError("executed selection differs from validated collection")
    counts = _execution_summary(phase.stdout)
    if counts.get("error", 0):
        raise IsolationError("pytest summary contains infrastructure errors")
    by_node = {node: {} for node in expected}
    for event in report["reports"]:
        if (not isinstance(event, dict) or event.get("nodeid") not in by_node
                or event.get("when") not in ("setup", "call", "teardown")
                or event.get("outcome") not in ("passed", "failed", "skipped")
                or type(event.get("wasxfail")) is not bool):
            raise IsolationError("malformed or foreign pytest execution report")
        events = by_node[event["nodeid"]]
        if event["when"] in events:
            raise IsolationError("duplicate execution report; retries are unsupported")
        events[event["when"]] = event
    observed = dict.fromkeys(("passed", "failed", "skipped", "xfailed", "xpassed"), 0)
    actual_failures = 0
    for events in by_node.values():
        if "setup" not in events or events.get("teardown", {}).get("outcome") != "passed":
            raise IsolationError("test setup or teardown was not accounted for")
        setup = events["setup"]
        if setup["outcome"] == "skipped":
            if "call" in events:
                raise IsolationError("skipped setup cannot have a test call")
            terminal = setup
        else:
            if "call" not in events:
                raise IsolationError("test body never produced a call report")
            terminal = events["call"]
        outcome = terminal["outcome"]
        if terminal["wasxfail"]:
            outcome = "xfailed" if outcome == "skipped" else "xpassed"
        observed[outcome] += 1
        if "call" in events and events["call"]["outcome"] == "failed":
            actual_failures += 1
    for outcome, count in observed.items():
        if counts.get(outcome, 0) != count:
            raise IsolationError("terminal summary disagrees with actual test reports")
    if sum(observed.values()) != len(expected) or counts.get("deselected", 0) != len(report["deselected"]):
        raise IsolationError("selected tests are not fully accounted for")
    # Independent guards: terminal failure claims and actual call failures must
    # both support exit 1. The composed mutation experiment exercises redundancy.
    if phase.returncode == 1 and counts.get("failed", 0) < 1:
        raise IsolationError("exit 1 has no failed test in its summary")
    if phase.returncode == 1 and actual_failures < 1:
        raise IsolationError("exit 1 has no actually-failed test call")
    if phase.returncode == 0 and actual_failures:
        raise IsolationError("exit 0 contradicts failed test reports")
    executed = sum("call" in events for events in by_node.values())
    supporting = sum(events.get("call", {}).get("outcome") in ("passed", "failed") for events in by_node.values())
    if not supporting:
        raise IsolationError("selection has no ordinary executed pass or failure; no outcome")
    if baseline and phase.returncode != 0:
        raise IsolationError("clean baseline did not pass")
    return ExecutionCheck("TEST_FAILURE" if phase.returncode == 1 else "TESTS_PASSED",
                          executed, actual_failures)


def _assess_mutation(sweep, relative, old, new, targets, environment, timeout, trace):
    sweep.scrub()
    plan = prepare_mutation(sweep.root, relative, old, new)
    if pathlib.Path(relative).suffix != ".py":
        raise IsolationError("target provenance supports Python source targets only")
    collection, report = _run_pytest(sweep, targets, environment, timeout, collect_only=True)
    trace.append(("collection", collection, report))
    nodes = validate_collection(sweep.root, collection, report)
    baseline, report = _run_pytest(sweep, targets, environment, timeout, target=plan.relative)
    trace.append(("baseline", baseline, report))
    validate_execution(baseline, report, nodes, baseline=True)
    mutant, report = _run_pytest(sweep, targets, environment, timeout, mutation=plan, target=plan.relative)
    trace.append(("mutant", mutant, report))
    return validate_execution(mutant, report, nodes)


@dataclass(frozen=True, slots=True)
class CheckedResult:
    signal: str
    detail: str
    evidence: pathlib.Path
    evidence_sha256: str
    status: str = field(default="UNVERIFIED", init=False)
    authoritative: bool = field(default=False, init=False)
    exit_code: int = field(default=1, init=False)


def _record_evidence(directory: pathlib.Path, payload: dict) -> tuple[pathlib.Path, str]:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (uuid.uuid4().hex + ".json")
    temporary = path.with_suffix(".tmp")
    try:
        with temporary.open("x") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            if sys.platform == "darwin":
                fcntl.fcntl(stream.fileno(), fcntl.F_FULLFSYNC)
    except OSError as exc:
        raise IsolationError("evidence publication failed; no result may be presented") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return path, _digest(path)


def _safe_trace(trace):
    phases = []
    for name, phase, report in trace:
        # Keep the actual decision inputs without raw tracebacks/local variables.
        try:
            summary = _summary_body(phase.stdout)
            if not re.fullmatch(r"[0-9a-zA-Z ,/()]+", summary):
                summary = None
        except IsolationError:
            summary = None
        phases.append({"phase": name, "returncode": phase.returncode, "summary": summary,
                       "stdout_sha256": hashlib.sha256(phase.stdout.encode()).hexdigest(),
                       "stderr_sha256": hashlib.sha256(phase.stderr.encode()).hexdigest(),
                       "report": report})
    return phases


def run_checked_mutation(sweep, relative, old, new, targets, *, environment, timeout, evidence_dir) -> CheckedResult:
    directory = pathlib.Path(evidence_dir).resolve()
    if directory.is_relative_to(sweep.entry.resolve()):
        raise IsolationError("durable evidence cannot live in the disposable sweep")
    trace = []
    try:
        check = _assess_mutation(sweep, relative, old, new, targets, environment, timeout, trace)
        signal_name, detail = check.signal, "execution checks passed; authority remains unverified"
    except (IsolationError, OSError, ValueError) as exc:
        signal_name, detail = "ERROR", str(exc) if isinstance(exc, IsolationError) else type(exc).__name__
    for root, replacement in ((sweep.root, "<execution-root>"), (sweep.source_repo, "<source-root>"),
                              (sweep.entry, "<sweep>")):
        detail = detail.replace(str(root), replacement)
    payload = {"version": 1, "status": "UNVERIFIED", "authoritative": False,
               "signal": signal_name, "detail": detail, "seed_sha": sweep.seed_sha,
               "old_sha256": hashlib.sha256(old.encode()).hexdigest(),
               "new_sha256": hashlib.sha256(new.encode()).hexdigest(),
               "phases": _safe_trace(trace)}
    path, digest = _record_evidence(directory, payload)
    return CheckedResult(signal_name, detail, path, digest)


def present_checked_result(result: CheckedResult) -> int:
    if result.signal not in ("ERROR", "TEST_FAILURE", "TESTS_PASSED"):
        raise IsolationError("unsupported diagnostic signal")
    try:
        digest = _digest(result.evidence)
    except OSError as exc:
        raise IsolationError("durable evidence is unavailable") from exc
    if digest != result.evidence_sha256:
        raise IsolationError("durable evidence changed before presentation")
    print(f"UNVERIFIED {result.signal}: {result.detail}")
    return result.exit_code


def _run_pytest(sweep, targets, environment, timeout, *, collect_only=False, mutation=None, target=None):
    report_path = sweep.entry / "reports" / (uuid.uuid4().hex + ".json")
    report_path.parent.mkdir(exist_ok=True)
    helper = pathlib.Path(__file__).with_name("pytest_evidence.py")
    bootstrap = "import runpy,sys; p=sys.argv.pop(1); runpy.run_path(p,run_name='__main__')"
    options = ["-q", "-o", "addopts=", "-p", "no:cacheprovider", "-p", "no:randomly",
               "-p", "no:rerunfailures", "-p", "no:flaky", "--color=no"]
    if collect_only:
        options.append("--collect-only")
    phase = sweep.run_phase(
        [sys.executable, "-c", bootstrap, str(helper), *options, *targets],
        environment={**environment, "PYTEST_ADDOPTS": "", "CWNG_PYTEST_EVIDENCE": str(report_path),
                     "CWNG_MEASURED_TARGET": str(sweep.root / target) if target else "",
                     "CWNG_MEASURED_ROOT": str(sweep.root)},
        timeout=timeout, ownership_contract=_TOKEN_CONTRACT, mutation=mutation,
        pytest_targets=targets,
    )
    try:
        report = json.loads(report_path.read_text())
    except (OSError, ValueError) as exc:
        raise IsolationError("pytest evidence missing or malformed") from exc
    if target:
        witness = report.get("target_provenance") if isinstance(report, dict) else None
        if (not isinstance(witness, dict) or witness.get("seen") is not True
                or witness.get("foreign") is not False or witness.get("active") is not True):
            raise IsolationError("target provenance REJECTED: missing, foreign or disabled execution witness")
    return phase, report


def _git(repo: pathlib.Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        raise IsolationError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout.strip()


def _is_live_pid(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _isolation_root(repo: pathlib.Path) -> pathlib.Path:
    identity = hashlib.sha256(str(repo.resolve()).encode()).hexdigest()[:16]
    return pathlib.Path(tempfile.gettempdir()) / "cwng-mutation-isolated" / identity


def _write_json(path: pathlib.Path, value: dict) -> None:
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _safe_sweep_entry(state_root: pathlib.Path, entry: pathlib.Path) -> bool:
    """Only allow cleanup of a direct child of our dedicated sweeps directory."""
    try:
        return entry.resolve().parent == (state_root.resolve() / "sweeps")
    except OSError:
        return False


def _reap_stale_sweeps(repo: pathlib.Path, state_root: pathlib.Path) -> list[pathlib.Path]:
    """Remove dead, tool-owned worktrees without touching a live sweep."""
    reaped: list[pathlib.Path] = []
    sweeps = state_root / "sweeps"
    sweeps.mkdir(parents=True, exist_ok=True)
    for entry in sweeps.iterdir():
        if not entry.is_dir() or not _safe_sweep_entry(state_root, entry):
            continue
        metadata_path = entry / "metadata.json"
        try:
            metadata = json.loads(metadata_path.read_text())
            worktree = pathlib.Path(metadata["worktree"])
            valid = (
                metadata.get("version") == _ISOLATION_VERSION
                and pathlib.Path(metadata["source_repo"]).resolve() == repo.resolve()
                and worktree.resolve() == (entry / "worktree").resolve()
            )
            owner = int(metadata["owner_pid"])
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if not valid or _is_live_pid(owner):
            continue
        removal = subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if removal.returncode and worktree.exists():
            continue
        shutil.rmtree(entry)
        reaped.append(worktree)
    _git(repo, "worktree", "prune", "--expire", "now")
    return reaped


class IsolatedSweep:
    """One disposable detached worktree seeded from an immutable commit."""

    def __init__(
        self,
        source_repo: pathlib.Path,
        state_root: pathlib.Path,
        entry: pathlib.Path,
        seed_sha: str,
        seed_tree: str,
    ) -> None:
        self.source_repo = source_repo.resolve()
        self.state_root = state_root.resolve()
        self.entry = entry.resolve()
        self.root = (entry / "worktree").resolve()
        self.seed_sha = seed_sha
        self.seed_tree = seed_tree
        self._closed = False
        self._phase_failed = False

    @classmethod
    def create(
        cls,
        source_repo: pathlib.Path,
        seed: str,
        *,
        state_root: pathlib.Path | None = None,
    ) -> "IsolatedSweep":
        source_repo = source_repo.resolve()
        state_root = (state_root or _isolation_root(source_repo)).resolve()
        state_root.mkdir(parents=True, exist_ok=True)
        lock_path = state_root / "allocation.lock"
        with lock_path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            _reap_stale_sweeps(source_repo, state_root)
            seed_sha = _git(source_repo, "rev-parse", "--verify", f"{seed}^{{commit}}")
            seed_tree = _git(source_repo, "rev-parse", f"{seed_sha}^{{tree}}")
            entry = state_root / "sweeps" / uuid.uuid4().hex
            entry.mkdir(parents=True)
            worktree = entry / "worktree"
            metadata = {
                "version": _ISOLATION_VERSION,
                "owner_pid": os.getpid(),
                "source_repo": str(source_repo),
                "worktree": str(worktree.resolve()),
                "seed_sha": seed_sha,
                "seed_tree": seed_tree,
                "state": "preparing",
            }
            _write_json(entry / "metadata.json", metadata)
            try:
                _git(source_repo, "worktree", "add", "--detach", str(worktree), seed_sha)
            except Exception:
                shutil.rmtree(entry, ignore_errors=True)
                _git(source_repo, "worktree", "prune", "--expire", "now")
                raise
            metadata["state"] = "active"
            _write_json(entry / "metadata.json", metadata)
        return cls(source_repo, state_root, entry, seed_sha, seed_tree)

    def scrub(self) -> dict[str, object]:
        """Restore every tracked and untracked path to the fixed seed commit."""
        if self._closed or not self.root.is_dir():
            raise IsolationError("disposable worktree is unavailable")
        _git(self.root, "reset", "--hard", self.seed_sha)
        _git(self.root, "clean", "-ffdx")
        head = _git(self.root, "rev-parse", "HEAD")
        status = _git(
            self.root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignored",
        )
        if head != self.seed_sha or status:
            raise IsolationError(
                "full phase scrub did not restore the pinned commit"
                + (f": {status}" if status else "")
            )
        return {
            "seed_sha": self.seed_sha,
            "seed_tree": self.seed_tree,
            "head": head,
            "status_empty": True,
        }

    def run_phase(
        self,
        argv: list[str],
        *,
        environment: dict[str, str],
        timeout: float,
        ownership_contract: str | None = None,
        mutation: MutationPlan | None = None,
        pytest_targets: list[str] | None = None,
    ) -> PhaseResult:
        """Scrub around a completed diagnostic phase, refusing reuse after an error.

        The default rejects arbitrary descendant containment through the runner.
        If cleanup cannot establish its restricted contract, preserve the tree
        rather than scrubbing underneath a potentially surviving writer.
        """
        if self._phase_failed:
            raise IsolationError("sweep cannot run another phase after an error")
        try:
            if ownership_contract != _TOKEN_CONTRACT or sys.platform != "darwin":
                raise IsolationError("arbitrary descendant containment is unavailable")
            self.scrub()
            if mutation is not None:
                apply_mutation(self.root, mutation)
            environment = provenance_environment(self.root, environment)
            targets = pytest_targets
            if targets is None and len(argv) >= 3 and argv[1:3] == ["-m", "pytest"]:
                targets = argv[3:]
            provenance_preflight(
                self.root, environment=environment, artifacts=self.entry / "provenance",
                pytest_targets=targets,
            )
            # Collection imports in preflight are writers. Discard their state
            # and restore the exact measured input after all probe processes end.
            self.scrub()
            if mutation is not None:
                apply_mutation(self.root, mutation)
                if _mutation_target(self.root, mutation.relative).read_bytes() != mutation.after:
                    raise IsolationError("measured mutation bytes changed before launch")
            result = run_phase_process(
                argv, cwd=self.root, environment=environment, timeout=timeout,
                artifacts=self.entry / "artifacts", ownership_contract=ownership_contract,
            )
            if result.containment_error:
                raise IsolationError(result.containment_error)
            self.scrub()
            if result.timed_out:
                raise IsolationError("phase timed out")
            return result
        except BaseException:
            self._phase_failed = True
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        removal = subprocess.run(
            [
                "git",
                "-C",
                str(self.source_repo),
                "worktree",
                "remove",
                "--force",
                str(self.root),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if removal.returncode and self.root.exists():
            detail = removal.stderr.strip() or removal.stdout.strip()
            raise IsolationError(f"could not remove disposable worktree: {detail}")
        shutil.rmtree(self.entry, ignore_errors=False)
        _git(self.source_repo, "worktree", "prune", "--expire", "now")

    def __enter__(self) -> "IsolatedSweep":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()


def _digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def legacy_journal(repo: pathlib.Path) -> pathlib.Path:
    # Match the retired P0.4a state-directory identity without creating it.
    key = hashlib.sha256(os.fsencode(repo.resolve())).hexdigest()[:20]
    return pathlib.Path(tempfile.gettempdir()) / "cwng-mutation" / key / "active.json"


def refuse_legacy_journal(repo: pathlib.Path) -> None:
    journal = legacy_journal(repo)
    if journal.exists() or journal.is_symlink():
        raise IsolationError(
            "legacy journal detected in temporary cwng-mutation/<repository-key>/active.json; "
            "preserve the journal, its recovery copies and the source checkout. "
            "Inspect the recorded target and compare recovery/source bytes; recover needed work "
            "before archiving the journal outside that active location. "
            "See tests/mutation/ISOLATION.md. No files were restored or deleted."
        )


def _cli_mutants(args):
    if args.spec:
        if args.file or args.old is not None or args.new is not None or args.test:
            raise IsolationError("--spec cannot be combined with inline mutation arguments")
        mutants = json.loads(pathlib.Path(args.spec).read_text())
    else:
        mutants = [{"name": args.name, "file": args.file, "old": args.old,
                    "new": args.new, "test": args.test}]
    if not isinstance(mutants, list) or not mutants:
        raise IsolationError("specification must contain at least one mutation")
    for item in mutants:
        if (not isinstance(item, dict) or
                not all(isinstance(item.get(key), str) for key in ("file", "old", "new"))):
            raise IsolationError("each mutation requires file, old and new strings")
        tests = item.get("test")
        if isinstance(tests, str):
            tests = [tests]
        if not isinstance(tests, list) or not tests or not all(isinstance(t, str) and t for t in tests):
            raise IsolationError("each mutation requires test targets")
        # v1 accepts committed, repository-relative node/file selections only.
        for value in [item["file"], *[t.split("::", 1)[0] for t in tests]]:
            path = pathlib.PurePosixPath(value)
            if not value or value.startswith("-") or path.is_absolute() or ".." in path.parts:
                raise IsolationError("mutation files and test targets must be repository-relative paths")
        item["test"] = tests
    return mutants


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file"); ap.add_argument("--old"); ap.add_argument("--new")
    ap.add_argument("--test", action="append", default=[])
    ap.add_argument("--spec")
    ap.add_argument("--name", default="mutant")
    ap.add_argument("--repo", type=pathlib.Path, default=REPO)
    ap.add_argument("--seed", required=True, help="committed seed; resolved once before any phase")
    ap.add_argument("--evidence-dir", type=pathlib.Path)
    ap.add_argument("--timeout", type=float, default=1800, help="bounded per-phase hang watchdog in seconds")
    args = ap.parse_args()
    try:
        repo = args.repo.resolve(strict=True)
        refuse_legacy_journal(repo)
        if not 0 < args.timeout < float("inf"):
            raise IsolationError("timeout must be positive and finite")
        mutants = _cli_mutants(args)
        evidence = (args.evidence_dir or (_isolation_root(repo) / "evidence")).resolve()
        # Never place durable output in the source checkout.
        if evidence.is_relative_to(repo):
            raise IsolationError("evidence directory must be outside the source checkout")
        print("UNVERIFIED diagnostic backend; committed seed only; shared Git writes UNSUPPORTED", flush=True)
        print("Outside boundary: temporary directories, venv, home, common Git data, Docker, "
              "databases, network, ports, caches, services and escaped processes", flush=True)
        with IsolatedSweep.create(repo, args.seed) as sweep:
            print(f"UNVERIFIED seed={sweep.seed_sha}", flush=True)
            for index, item in enumerate(mutants, 1):
                result = run_checked_mutation(
                    sweep, item["file"], item["old"], item["new"], item["test"],
                    environment=os.environ.copy(), timeout=args.timeout, evidence_dir=evidence,
                )
                present_checked_result(result)
                print(f"UNVERIFIED observation={index} evidence={result.evidence.name}", flush=True)
                if result.signal == "ERROR":
                    break
        return 1
    except (IsolationError, OSError, ValueError) as exc:
        detail = str(exc) if isinstance(exc, IsolationError) else type(exc).__name__
        print(f"UNVERIFIED ERROR: {detail}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
