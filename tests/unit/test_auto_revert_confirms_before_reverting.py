# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""auto-revert must not destroy work on a failure it never confirmed.

WHAT WENT WRONG (2026-08-12). `Test Suite` went red on a main push, run
31648495770, and auto-revert opened #1575 to revert `2afd721a` — 531 lines of
the working reader-search feature. The failure was:

    FAILED tests/unit/test_ingest_startup_sweep_1360.py::
           test_startup_sweep_leaves_a_still_growing_file_to_the_watcher
    1 failed, 6061 passed, 72 skipped

A bash ingest-timing probe. The reverted commit changed `frontend/` and
`cps/spa_strings.py`, which that test does not read, and main is green on that
same commit today (`fab10acd`).

WHY THE EXISTING GUARDS MISSED IT. Both answer "could this commit have caused
it?" from static facts, and both said yes, correctly:

  * guard 1 (path allowlist) — the commit is not `findings/`-only, so it is
    capable of breaking *something*.
  * guard 2 (job-name filter, #1564) — the failure was `Fast Tests`, not the
    SPA e2e lane it was written to exclude.

Neither can see a FLAKE. A flaky test makes every commit look guilty in turn,
indefinitely, at whatever rate it flakes, and narrowing guard 2 lane by lane
chases the symptom: the next flake on a third lane repeats this exactly.

THE GUARD THESE TESTS PIN. A failure must REPRODUCE on the same tree before it
justifies reverting. The re-run delivers its own verdict by re-entering this
workflow with `run_attempt` incremented, so no polling is needed:

    attempt 1, revert-worthy  -> re-run the failed jobs, revert nothing
    attempt >= 2              -> failed twice on the same tree; revert

A flake that clears makes the re-run green, so the job's `if:` never fires.
The attempt check is also the loop bound — attempt 2 never re-runs again.

These tests EXECUTE the workflow's shell with a stubbed `gh` rather than
grepping it, because the guard is a shell decision and only running it proves
which way the decision goes.
"""

from __future__ import annotations

import os
import re
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

CONFIRM_STEP_ID = "confirm"
TRIAGE_STEP_ID = "triage"


def _job() -> dict:
    if yaml is None:  # pragma: no cover
        pytest.skip("PyYAML not installed in this environment")
    assert WORKFLOW.is_file(), (
        "auto-revert.yml is missing — the red-main safety net is gone"
    )
    with WORKFLOW.open() as fh:
        wf = yaml.safe_load(fh) or {}
    job = (wf.get("jobs") or {}).get("revert-on-red")
    assert isinstance(job, dict), "auto-revert.yml must define a revert-on-red job"
    return job


def _step(step_id: str) -> dict:
    steps = [s for s in (_job().get("steps") or []) if isinstance(s, dict)]
    step = next((s for s in steps if s.get("id") == step_id), None)
    assert step is not None, (
        f"auto-revert.yml has no step with id `{step_id}`. The revert decision "
        "must be a named, testable step — an inlined or renamed one silently "
        "drops the guard these tests exist to hold."
    )
    return step


def _run_confirm(run_attempt: str, gh_exit: int = 0, tmp_path: Path = None):
    """Execute the confirmation step's script the way Actions would.

    Returns (exit code, parsed GITHUB_OUTPUT dict, combined output, argv the
    stubbed `gh` was called with). `gh` is stubbed on PATH so the test can
    assert whether a re-run was actually requested, without touching GitHub.
    """
    script = str(_step(CONFIRM_STEP_ID).get("run") or "")
    assert script.strip(), "the confirmation step must have a `run:` script"

    # The step reads its inputs from env: (never `${{ }}` inside run:, which is
    # the workflow-injection shape). Anything left interpolated is unmodelled.
    leftover = re.findall(r"\$\{\{.*?\}\}", script)
    assert not leftover, (
        f"unmodelled workflow expressions in the confirmation script: {leftover}. "
        "Inputs must arrive via env: so they are quoted, not interpolated."
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_log = tmp_path / "gh-argv.log"
    gh_stub = bin_dir / "gh"
    gh_stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{gh_log}"\n'
        f"exit {gh_exit}\n"
    )
    gh_stub.chmod(0o755)

    github_output = tmp_path / "github_output"
    github_output.write_text("")

    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "GITHUB_OUTPUT": str(github_output),
            "GITHUB_REPOSITORY": "new-usemame/Calibre-Web-NextGen",
            "GH_TOKEN": "stub-token",
            "RUN_ID": "31648495770",
            "RUN_ATTEMPT": run_attempt,
        },
    )
    outputs = dict(
        line.split("=", 1)
        for line in github_output.read_text().splitlines()
        if "=" in line
    )
    gh_calls = gh_log.read_text().splitlines() if gh_log.exists() else []
    return proc.returncode, outputs, proc.stdout + proc.stderr, gh_calls


# ─── The behaviour: one failure is not evidence ────────────────────────────


def test_a_single_failure_does_not_revert(tmp_path):
    """THE #1575 symptom. The first time a commit's run goes red, the guard
    must decline to revert — that run is one sample, and a flake produces
    exactly this picture.

    RED before the fix: there is no confirmation step at all, so a
    revert-worthy triage went straight to opening the revert PR."""
    rc, outputs, out, _ = _run_confirm("1", tmp_path=tmp_path)

    assert rc == 0, f"the confirmation step must not fail the job:\n{out}"
    assert outputs.get("confirmed") == "false", (
        "a first, unreproduced failure was treated as confirmed — that is what "
        f"deleted 531 lines of working code in #1575. outputs={outputs}"
    )


def test_a_single_failure_triggers_a_rerun_of_the_same_tree(tmp_path):
    """Declining to revert is only half of it. Nothing else re-runs the suite,
    so without this the commit is left un-adjudicated and a REAL breakage sits
    on main with no second verdict coming."""
    _, _, out, gh_calls = _run_confirm("1", tmp_path=tmp_path)

    assert gh_calls, f"no `gh` invocation — the failed jobs were never re-run:\n{out}"
    joined = " ".join(gh_calls)
    assert "run rerun" in joined, f"expected a `gh run rerun`, got: {gh_calls}"
    assert "31648495770" in joined, (
        f"the re-run must target the failing run itself, got: {gh_calls}"
    )
    assert "--failed" in joined, (
        "re-run only the failed jobs — a full re-run costs the whole matrix to "
        f"answer a question about one job. got: {gh_calls}"
    )


def test_a_failure_that_reproduces_is_reverted(tmp_path):
    """The guard must not become a blanket refusal. A commit that fails the
    same way on a fresh run of the same tree is genuinely guilty, and reverting
    it is this workflow's whole purpose."""
    rc, outputs, out, _ = _run_confirm("2", tmp_path=tmp_path)

    assert rc == 0, f"the confirmation step must not fail the job:\n{out}"
    assert outputs.get("confirmed") == "true", (
        "a failure reproduced on a second attempt was still not acted on — the "
        f"safety net is now disabled and main stays red. outputs={outputs}"
    )


def test_the_second_attempt_does_not_rerun_again(tmp_path):
    """The loop bound. Each re-run completes and re-enters this workflow, so a
    guard that re-ran on every attempt would re-run forever on a genuinely
    broken commit, burning the matrix each time and never reverting."""
    _, _, _, gh_calls = _run_confirm("2", tmp_path=tmp_path)

    assert not gh_calls, (
        "attempt 2 requested another re-run. Every re-run re-enters this "
        f"workflow, so this is an unbounded loop. calls={gh_calls}"
    )


def test_a_rerun_that_cannot_be_requested_fails_closed(tmp_path):
    """Same posture the triage step already takes: when the signal cannot be
    read, prefer the loud failure. A revert we skip leaves main red, which is
    visible; a revert we open wrongly deletes correct work quietly."""
    rc, outputs, out, _ = _run_confirm("1", gh_exit=1, tmp_path=tmp_path)

    assert rc == 0, (
        f"a failed `gh` call must not fail the job — `set -e` would skip the\n"
        f"output write and leave the decision unset:\n{out}"
    )
    assert outputs.get("confirmed") == "false", (
        "the re-run could not even be requested, so the failure is still "
        f"unconfirmed — reverting on it is a guess. outputs={outputs}"
    )


# ─── The wiring: the guard must actually gate the destructive step ─────────


def test_the_revert_pr_step_is_gated_on_confirmation():
    """A guard that computes a verdict nothing reads is decoration. This is the
    one assertion the behavioural tests above cannot make, because it is about
    the step graph rather than the shell."""
    steps = [s for s in (_job().get("steps") or []) if isinstance(s, dict)]
    pr_steps = [
        s
        for s in steps
        if "gh pr create" in str(s.get("run") or "")
    ]
    assert pr_steps, "auto-revert.yml no longer opens a revert PR"

    for step in pr_steps:
        condition = str(step.get("if") or "")
        assert f"steps.{CONFIRM_STEP_ID}.outputs.confirmed" in condition, (
            "the revert PR step does not read the confirmation verdict, so an "
            "unreproduced failure can still destroy work. This is exactly the "
            f"#1575 shape. if: {condition!r}"
        )
        assert f"steps.{TRIAGE_STEP_ID}.outputs.revert" in condition, (
            "the existing triage gate (path allowlist + e2e-lane filter) must "
            f"survive alongside the new one. if: {condition!r}"
        )


def test_confirmation_does_not_widen_workflow_permissions():
    """Re-running jobs is an `actions: write` capability, and the tempting way
    to get it is to grant that scope. It is not needed: the step authenticates
    with secrets.GH_PAT exactly as the PR-open step does.

    `test_no_workflow_grants_dangerous_permissions` in
    test_workflow_safety_invariants.py owns this rule globally; this pins the
    reason locally, so a future edit that "fixes" a re-run permission error by
    widening the scope has to argue with the file it is editing."""
    if yaml is None:  # pragma: no cover
        pytest.skip("PyYAML not installed in this environment")
    with WORKFLOW.open() as fh:
        wf = yaml.safe_load(fh) or {}

    top = wf.get("permissions") or {}
    assert not (isinstance(top, dict) and top.get("actions") == "write"), (
        "auto-revert.yml granted permissions.actions: write. The re-run rides "
        "on secrets.GH_PAT instead — keep GITHUB_TOKEN narrow."
    )
    assert (_step(CONFIRM_STEP_ID).get("env") or {}).get("GH_TOKEN"), (
        "the confirmation step must set GH_TOKEN explicitly (to secrets.GH_PAT); "
        "falling back to the default token would need the wider scope."
    )
