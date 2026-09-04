#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run a mutation and report whether the suite actually caught it.

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
    a SURVIVING mutant exits 1       -> the gap is the finding, not the pass

Usage:
    mutate.py --file F --old STR --new STR --test TARGET [--test TARGET ...]
    mutate.py --spec mutants.json          # [{name, file, old, new, test}, ...]
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import uuid

REPO = pathlib.Path(__file__).resolve().parents[2]
_ISOLATION_VERSION = 1


class IsolationError(RuntimeError):
    """The disposable execution tree could not be made trustworthy."""


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
        caught = proc.returncode != 0
        tail = [ln for ln in proc.stdout.strip().splitlines() if " passed" in ln or " failed" in ln]
        summary = tail[-1] if tail else "(no pytest summary)"
    finally:
        shutil.copy2(backup, target)
        backup.unlink(missing_ok=True)

    restored = _digest(target)
    if restored != before:
        return {"name": name, "status": "ERROR",
                "detail": "RESTORE FAILED — working tree is dirty, fix before continuing"}

    return {"name": name, "status": "caught" if caught else "SURVIVED", "summary": summary}


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
        mark = {"caught": "caught  ", "SURVIVED": "SURVIVED", "ERROR": "ERROR   "}[r["status"]]
        print(f"  {mark}  {r['name']}  {r.get('summary', r.get('detail',''))}", flush=True)

    survived = [r for r in results if r["status"] == "SURVIVED"]
    errored = [r for r in results if r["status"] == "ERROR"]
    print(f"\n{len(results)} mutant(s): {len(results)-len(survived)-len(errored)} caught, "
          f"{len(survived)} SURVIVED, {len(errored)} error")
    if errored:
        print("errors are not verdicts — the mutant never ran; fix the anchor and re-run")
    return 1 if (survived or errored) else 0


if __name__ == "__main__":
    sys.exit(main())
