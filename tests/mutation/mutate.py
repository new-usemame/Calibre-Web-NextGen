#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run mutation diagnostics and emit UNVERIFIED observations.

Mutation testing by hand is the most error-prone thing in this repo's workflow,
and every one of its failure modes reports as a PASS. Measured on 2026-08-29,
in a single session, doing it with `cp` and `sed`:

* An anchor that no longer matched left the mutant UNAPPLIED. pytest printed a
  green summary, which is indistinguishable from "the test caught nothing".
* `cp cps/admin.py /tmp/$(basename …)` and `cp cps/api/admin.py /tmp/$(basename …)`
  both wrote `/tmp/admin.py`. The restore then put a 476-line SPA module on top
  of a 3,788-line one, and the tree only failed at import.
* A restore was assumed rather than checked; a `git checkout --` on a file whose
  fix was uncommitted silently discarded it.

So this tool refuses to report a result it cannot stand behind:

    anchor must match exactly once   -> otherwise ERROR, never a verdict
    backups are path-derived         -> cps/admin.py and cps/api/admin.py differ
    restore is hash-verified         -> and failure is loud
    diagnostic observations exit 1  -> they cannot satisfy an authoritative gate

Usage:
    mutate.py --file F --old STR --new STR --test TARGET [--test TARGET ...]
    mutate.py --spec mutants.json          # [{name, file, old, new, test}, ...]
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass, field
import fcntl
import hashlib
import json
import os
import pathlib
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
class DiagnosticObservation(Mapping):
    """An immutable observation; no constructor or replacement can grant authority.

    Mutants are abnormal input: conversion can erase the token while its wrapper
    starts a new session. Only a future strong backend may supply verdicts.
    """
    name: str
    returncode: int | None
    summary: str
    status: str = field(default="UNVERIFIED", init=False)
    authoritative: bool = field(default=False, init=False)

    def __iter__(self):
        return iter(("name", "returncode", "summary", "status", "authoritative"))

    def __len__(self):
        return 5

    def __getitem__(self, key):
        if key not in tuple(self):
            raise KeyError(key)
        return getattr(self, key)


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
    The command-line mutation flow does not opt in to this weaker contract.
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
            raise IsolationError(f"provenance REJECTED: {shape} probe execution failed")
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
            environment = provenance_environment(self.root, environment)
            targets = argv[3:] if len(argv) >= 3 and argv[1:3] == ["-m", "pytest"] else None
            provenance_preflight(
                self.root, environment=environment, artifacts=self.entry / "provenance",
                pytest_targets=targets,
            )
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


def _backup_name(rel: str) -> str:
    """Path-derived, so same-named modules in different packages never collide."""
    return "mut_" + rel.replace("/", "_").replace("\\", "_")


def run_mutant(name, rel_file, old, new, tests, quiet=False):
    target = REPO / rel_file
    if not target.is_file():
        return {"name": name, "status": "ERROR", "detail": f"no such file: {rel_file}"}

    source = target.read_text()
    hits = source.count(old)
    if hits != 1:
        # The failure that looks exactly like success. Never return a verdict.
        return {"name": name, "status": "ERROR",
                "detail": f"anchor matched {hits} times, expected exactly 1 — mutant NOT applied"}

    before = _digest(target)
    backup = pathlib.Path(tempfile.gettempdir()) / _backup_name(rel_file)
    shutil.copy2(target, backup)

    try:
        target.write_text(source.replace(old, new, 1))
        if _digest(target) == before:
            return {"name": name, "status": "ERROR", "detail": "file unchanged after write"}
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *tests, "-q", "-p", "no:randomly"],
            cwd=REPO, capture_output=True, text=True, timeout=1800)
        tail = [ln for ln in proc.stdout.strip().splitlines() if " passed" in ln or " failed" in ln]
        summary = tail[-1] if tail else "(no pytest summary)"
    finally:
        shutil.copy2(backup, target)
        backup.unlink(missing_ok=True)

    restored = _digest(target)
    if restored != before:
        return {"name": name, "status": "ERROR",
                "detail": "RESTORE FAILED — working tree is dirty, fix before continuing"}

    return DiagnosticObservation(name=name, returncode=proc.returncode, summary=summary)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file"); ap.add_argument("--old"); ap.add_argument("--new")
    ap.add_argument("--test", action="append", default=[])
    ap.add_argument("--spec")
    ap.add_argument("--name", default="mutant")
    args = ap.parse_args()

    if args.spec:
        mutants = json.loads(pathlib.Path(args.spec).read_text())
    else:
        if not (args.file and args.old is not None and args.new is not None and args.test):
            ap.error("need --file, --old, --new and at least one --test (or --spec)")
        mutants = [{"name": args.name, "file": args.file, "old": args.old,
                    "new": args.new, "test": args.test}]

    results = []
    for m in mutants:
        tests = m["test"] if isinstance(m["test"], list) else [m["test"]]
        r = run_mutant(m.get("name", m["file"]), m["file"], m["old"], m["new"], tests)
        results.append(r)
        # This harness has no strong backend. Even stale caller-supplied labels
        # cannot promote an observation at the terminal boundary.
        mark = "ERROR" if r["status"] == "ERROR" else "UNVERIFIED"
        print(f"  {mark}  {r['name']}  {r.get('summary', r.get('detail',''))}", flush=True)

    errored = sum(r["status"] == "ERROR" for r in results)
    print(f"\n{len(results)} observation(s): {len(results)-errored} UNVERIFIED, {errored} error")
    print("UNVERIFIED: no strong containment backend; diagnostic observations are not verdicts")
    return 1


if __name__ == "__main__":
    sys.exit(main())
