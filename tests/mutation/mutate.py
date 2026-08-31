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
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]


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
