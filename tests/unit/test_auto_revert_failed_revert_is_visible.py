# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""auto-revert must not report success when it reverted nothing (#2553).

The revert-PR step ran `git revert ... || { echo "Revert failed; aborting";
exit 0; }`. A revert that conflicts with later commits on main therefore
ended the job green: no branch, no PR, main still red, and the run that was
supposed to be the safety net said everything went fine.

Two different things make `git revert` exit non-zero, and only one of them is
a failure:

  * a conflict (or anything else that leaves the revert undone) -- main is
    red and nobody is told. That must fail the job.
  * the commit is already reverted on main -- git refuses an empty commit
    and exits 1 with a clean tree. Nothing is left to do, so that is a
    clean, explicit no-op.

A bad SHA also leaves a clean tree, so "the tree is clean" cannot be the
test for the no-op case; the step has to establish that the commit's paths
already match its parent.

These tests run the step's own shell against real git repositories with a
stubbed `gh`, because the behaviour is a shell decision and only running it
shows which way it goes.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "auto-revert.yml"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def _revert_pr_script() -> str:
    if yaml is None:  # pragma: no cover
        pytest.skip("PyYAML not installed in this environment")
    with WORKFLOW.open() as fh:
        wf = yaml.safe_load(fh) or {}
    steps = wf["jobs"]["revert-on-red"]["steps"]
    matches = [s for s in steps if "gh pr create" in str(s.get("run") or "")]
    assert len(matches) == 1, "expected exactly one step that opens the revert PR"
    return str(matches[0]["run"])


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV},
    ).stdout.strip()


def _commit(cwd: Path, message: str, **files: str) -> str:
    for name, body in files.items():
        (cwd / name).write_text(body)
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", message)
    return _git(cwd, "rev-parse", "HEAD")


def _main_with_bad_commit(tmp_path: Path, after_bad) -> tuple[Path, Path, str]:
    """origin (bare) + a checkout of main whose history is A, BAD, then
    whatever `after_bad` adds. Returns (checkout, origin, BAD sha)."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(origin), str(work))
    _git(work, "checkout", "-q", "-b", "main")
    _commit(work, "A", **{"f.txt": "one\n", "g.txt": "keep\n"})
    bad = _commit(work, "BAD: break the suite", **{"f.txt": "two\n"})
    after_bad(work, bad)
    _git(work, "push", "-q", "origin", "main")
    return work, origin, bad


def _run_step(work: Path, bad: str, tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_log = tmp_path / "gh-argv.log"
    gh = bin_dir / "gh"
    gh.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{gh_log}"\n')
    gh.chmod(0o755)

    # GitHub's default `run:` shell.
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _revert_pr_script()],
        cwd=work,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            **GIT_ENV,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GH_TOKEN": "stub-token",
            "BAD_SHA": bad,
            "RUN_URL": "https://example.invalid/run/1",
        },
    )
    gh_calls = gh_log.read_text().splitlines() if gh_log.exists() else []
    return proc.returncode, proc.stdout + proc.stderr, gh_calls


def _remote_branches(origin: Path) -> list[str]:
    out = _git(origin, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return [b for b in out.splitlines() if b]


def test_a_revert_that_conflicts_fails_the_job(tmp_path):
    """THE #2553 symptom: a later commit rewrote the same lines, the revert
    conflicts, and the run must go red rather than report success."""
    work, origin, bad = _main_with_bad_commit(
        tmp_path, lambda w, _: _commit(w, "later edit", **{"f.txt": "three\n"})
    )

    rc, out, gh_calls = _run_step(work, bad, tmp_path)

    assert rc != 0, (
        "the revert conflicted and nothing was reverted, yet the step exited 0, "
        f"so the run shows green while main stays red:\n{out}"
    )
    assert not gh_calls, f"no revert exists, so no PR may be opened: {gh_calls}"
    assert _remote_branches(origin) == ["main"], "nothing may be pushed"


def test_an_already_reverted_commit_is_a_clean_no_op(tmp_path):
    """Main already carries the revert. There is nothing to do, and that is
    not a failure: exit 0, push nothing, open no PR, and say why."""

    def revert_it(w, bad):
        _git(w, "revert", "--no-edit", bad)

    work, origin, bad = _main_with_bad_commit(tmp_path, revert_it)

    rc, out, gh_calls = _run_step(work, bad, tmp_path)

    assert rc == 0, f"an already-reverted commit is not a failure:\n{out}"
    assert "already reverted" in out.lower(), (
        f"the no-op must be explicit in the log, not silent:\n{out}"
    )
    assert not gh_calls, f"nothing to revert, so no PR: {gh_calls}"
    assert _remote_branches(origin) == ["main"], "nothing may be pushed"


def test_an_unknown_commit_is_a_failure_not_a_no_op(tmp_path):
    """A SHA the checkout does not have also makes `git revert` fail with a
    clean tree. Reading that as "already reverted" would hide exactly the
    failure this step must report."""
    work, origin, _ = _main_with_bad_commit(tmp_path, lambda w, _: None)

    rc, out, gh_calls = _run_step(work, "0" * 40, tmp_path)

    assert rc != 0, f"an unknown commit was treated as a clean no-op:\n{out}"
    assert not gh_calls
    assert _remote_branches(origin) == ["main"]


def test_a_clean_revert_still_opens_the_pr(tmp_path):
    """The point of the workflow. A later commit that touches other files
    leaves the revert clean: the branch is pushed and the PR is opened."""
    work, origin, bad = _main_with_bad_commit(
        tmp_path, lambda w, _: _commit(w, "unrelated", **{"g.txt": "changed\n"})
    )

    rc, out, gh_calls = _run_step(work, bad, tmp_path)

    assert rc == 0, out
    assert f"auto-revert/{bad[:7]}" in _remote_branches(origin), out
    assert any(c.startswith("pr create") for c in gh_calls), gh_calls
    assert (work / "f.txt").read_text() == "one\n"


def test_a_merge_commit_that_cannot_be_reverted_is_a_failure(tmp_path):
    """`git revert` refuses a merge commit without `-m` and leaves a clean
    tree, the same picture as an already-reverted commit. Only the content
    check tells them apart; a step that reads "clean tree" as "nothing to do"
    reports success with main still red."""

    def merge_in(w, _):
        _git(w, "checkout", "-q", "-b", "side", "HEAD~1")
        _commit(w, "side work", **{"h.txt": "side\n"})
        _git(w, "checkout", "-q", "main")
        _git(w, "merge", "-q", "--no-ff", "--no-edit", "side")

    work, origin, _ = _main_with_bad_commit(tmp_path, merge_in)
    merge = _git(work, "rev-parse", "HEAD")

    rc, out, gh_calls = _run_step(work, merge, tmp_path)

    assert rc != 0, f"an unrevertable merge was reported as a clean no-op:\n{out}"
    assert not gh_calls
    assert _remote_branches(origin) == ["main"]
