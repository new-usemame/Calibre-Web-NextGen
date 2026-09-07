# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Behavioural test of the `Test Suite Summary` gate, by EXECUTING its shell.

Why this exists rather than another static assertion about the YAML: the defect
it guards against is not a missing string, it is a *decision*. The old summary
enumerated the bad state (`result == "failure"`) instead of requiring the good
one, so on a push to main every other state — `skipped`, `cancelled` — fell
through to `exit 0`. The required check went green having verified nothing, and
that is exactly how the SPA e2e suite came to never run on main without anyone
noticing (F-54d342).

A gate written as "nothing was bad" admits every state that is neither good nor
bad. Reading the YAML confirms the intent you already hold; running it is what
tells you which states actually pass. So this extracts the real `run:` block and
executes it under substituted job results.

Includes a POSITIVE CONTROL (all-success must exit 0). Without one, a harness
that rejects everything would look like a perfect gate.
"""
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

# Same convention as test_workflow_safety_invariants.py.
REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"


def _summary_shell():
    text = WORKFLOW.read_text(encoding="utf-8")
    m = re.search(
        r"- name: Check test results\n(.*?)\n        run: \|\n(.*?)"
        r"(?=\n      - name:|\n  [a-z_]+:\n|\Z)",
        text,
        re.S,
    )
    assert m, "could not locate the Test Suite Summary 'Check test results' run block"
    body = m.group(2)
    return "\n".join(
        line[10:] if line.startswith(" " * 10) else line for line in body.split("\n")
    )


def _render(shell, *, event, ref, fast, build, integration, e2e,
            is_frontend_pr="false", is_tier2="false", is_build_pr="false",
            is_concurrency_pr="false", changed_paths="success", impact_map="success"):
    subs = {
        "needs.fast-tests.result": fast,
        "needs.frontend-build.result": build,
        "needs.integration-tests.result": integration,
        "needs.e2e-tests.result": e2e,
        "needs.changed_paths.result": changed_paths,
        "needs.impact-map.result": impact_map,
        "github.event_name": event,
        "github.ref": ref,
    }
    # GitHub only exposes results for jobs declared in this job's needs.
    # Executing with that context makes the positive control catch a missing
    # dependency even if the shell's success predicate itself is correct.
    dependencies = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["test-summary"]["needs"]
    if isinstance(dependencies, str):
        dependencies = [dependencies]
    for key, value in subs.items():
        if key.startswith("needs.") and key.split(".")[1] not in dependencies:
            value = ""
        shell = shell.replace("${{ " + key + " }}", value)
    # Any residual GitHub expression is a boolean we are not exercising.
    shell = re.sub(r"\$\{\{[^}]*\}\}", "false", shell)
    is_main_push = "true" if (event == "push" and ref == "refs/heads/main") else "false"
    env = (
        f"IS_TIER2_PR={is_tier2}\n"
        f"IS_BUILD_PR={is_build_pr}\n"
        f"IS_FRONTEND_PR={is_frontend_pr}\n"
        f"IS_CONCURRENCY_PR={is_concurrency_pr}\n"
        f"IS_MAIN_PUSH={is_main_push}\n"
    )
    return "set -o pipefail\n" + env + shell


def _run(**kwargs):
    script = _render(_summary_shell(), **kwargs)
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return proc.returncode, proc.stdout


MAIN_PUSH = dict(event="push", ref="refs/heads/main")


@pytest.mark.parametrize("summary_failed", [False, True])
def test_impact_map_failure_never_authorizes_auto_revert(tmp_path, summary_failed):
    """Intent: an impact-map failure and its propagated summary failure cannot revert an innocent commit."""
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/auto-revert.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["revert-on-red"]["steps"]
    triage = next(step["run"] for step in steps if step.get("id") == "triage")
    suite = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = [{"name": job["name"], "conclusion": "success"}
            for job in suite["jobs"].values() if "name" in job]
    impact_name = suite["jobs"]["impact-map"]["name"]
    for job in jobs:
        if job["name"] == impact_name or (summary_failed and job["name"] == "Test Suite Summary"):
            job["conclusion"] = "failure"

    def decide(job_list):
        output = tmp_path / "github-output"
        output.write_text("", encoding="utf-8")
        # Execute the entire triage shell and real jq; only external reads are stubbed.
        stubs = 'git() { printf "%s\\n" "cps/annotations.py"; }\ngh() { printf "%s\\n" "$JOBS_JSON"; }\n'
        result = subprocess.run(
            ["bash", "-c", stubs + triage], capture_output=True, text=True,
            env={**os.environ, "JOBS_JSON": json.dumps({"jobs": job_list}),
                 "GITHUB_OUTPUT": str(output), "RUN_ID": "1", "HEAD_SHA": "fixture",
                 "GITHUB_REPOSITORY": "fixture/project"},
        )
        assert result.returncode == 0, result.stderr
        return output.read_text(encoding="utf-8").strip(), result.stdout

    decision, out = decide(jobs)
    assert decision == "revert=false", out
    # Preserve the existing SPA exclusion and real product-failure signals.
    decision, out = decide([*jobs, {"name": "E2E Tests (SPA)", "conclusion": "failure"}])
    assert decision == "revert=false", out
    for job_id in ("fast-tests", "frontend-build", "integration-tests"):
        name = suite["jobs"][job_id]["name"]
        decision, out = decide([*jobs, {"name": name, "conclusion": "failure"}])
        assert decision == "revert=true", out


@pytest.mark.parametrize("event,ref", [
    ("pull_request", "refs/pull/1/merge"),
    ("push", "refs/heads/main"),
    ("push", "refs/heads/dev"),
    ("push", "refs/tags/v1.0.0"),
    ("workflow_dispatch", "refs/heads/main"),
])
@pytest.mark.parametrize("result", ["success", "failure", "skipped", "cancelled"])
def test_summary_requires_impact_map_success_on_every_trigger(event, ref, result):
    """Intent: real generation errors block the required gate; successful advisory staleness can pass."""
    rc, out = _run(
        event=event, ref=ref, fast="success", build="success",
        integration="success", e2e="success", impact_map=result,
    )
    expected = 0 if result == "success" else 1
    assert rc == expected, f"{event} with impact-map={result}: expected exit {expected}, got {rc}\n{out}"
    if result != "success":
        assert "Impact map" in out and result in out


def test_positive_control_all_success_passes():
    """The control. Without it, a harness that fails everything looks perfect."""
    rc, _ = _run(**MAIN_PUSH, fast="success", build="success",
                 integration="success", e2e="success")
    assert rc == 0


@pytest.mark.parametrize("e2e_result", ["failure", "skipped", "cancelled"])
def test_main_push_requires_e2e_success_not_merely_absence_of_failure(e2e_result):
    """Every non-success e2e result must fail the required summary on main.

    `skipped` and `cancelled` are the ones that matter: those are what a broken
    job condition produces, and letting them through would silently turn the
    coverage off while this check stays green — reinstating F-54d342 with a fix
    apparently in place.
    """
    rc, out = _run(**MAIN_PUSH, fast="success", build="success",
                   integration="success", e2e=e2e_result)
    assert rc == 1, f"main push with e2e={e2e_result} must fail the summary; got rc=0\n{out}"
    assert e2e_result in out, "the summary should name the actual result it rejected"


@pytest.mark.parametrize("job,kwargs", [
    ("fast-tests", dict(fast="skipped", build="success", integration="success", e2e="success")),
    ("frontend-build", dict(fast="success", build="cancelled", integration="success", e2e="success")),
])
def test_main_push_requires_the_other_hard_gated_lanes_too(job, kwargs):
    rc, out = _run(**MAIN_PUSH, **kwargs)
    assert rc == 1, f"main push with {job} not succeeding must fail the summary\n{out}"


def test_integration_stays_advisory_on_main():
    """Deliberate carve-out: integration-tests' own continue-on-error evaluates
    true on a main push, so hard-gating it here would change existing behaviour
    and block the auto-revert path the workflow documents."""
    rc, _ = _run(**MAIN_PUSH, fast="success", build="success",
                 integration="skipped", e2e="success")
    assert rc == 0


@pytest.mark.parametrize("result", ["failure", "skipped", "cancelled"])
def test_pull_request_requires_fast_tests_success(result):
    """The PR path must reject every non-success fast-tests result, not just
    `failure`.

    `cancelled` is not a hypothetical. A job killed by its own
    `timeout-minutes` is reported as **cancelled**, never as failure. OBSERVED
    2026-08-18 on #1725: the `Install system dependencies` apt step stalled for
    24m46s, the job's 25-minute cap fired, `Fast Tests` came back `cancelled`,
    and this summary — the single required check branch protection reads —
    exited 0 having run no tests at all.

    The strictness already existed for pushes to main and was never extended to
    pull requests, which is where essentially every merge is gated.
    """
    rc, out = _run(event="pull_request", ref="refs/heads/topic",
                   fast=result, build="success", integration="skipped",
                   e2e="skipped")
    assert rc == 1, (
        f"pull request with fast-tests={result} must fail the summary; got rc=0\n{out}"
    )
    assert result in out, "the summary should name the actual result it rejected"


@pytest.mark.parametrize("result", ["failure", "skipped", "cancelled"])
def test_pull_request_requires_frontend_build_success(result):
    """Same reasoning for the SPA bundle: it has no `if:`, so it runs on every
    PR, and any result other than success means it did not vouch for anything.
    """
    rc, out = _run(event="pull_request", ref="refs/heads/topic",
                   fast="success", build=result, integration="skipped",
                   e2e="skipped")
    assert rc == 1, (
        f"pull request with frontend-build={result} must fail the summary; got rc=0\n{out}"
    )


@pytest.mark.parametrize("result", ["skipped", "cancelled"])
def test_pull_request_requires_changed_paths_success(result):
    """`changed_paths` feeds BOTH path-derived gates. The workflow already
    rejects its `failure`; a cancelled or skipped run leaves its outputs just as
    empty, so both gates silently read "not applicable" and this summary would
    vouch for a run that gated nothing.
    """
    rc, out = _run(event="pull_request", ref="refs/heads/topic",
                   fast="success", build="success", integration="skipped",
                   e2e="skipped", changed_paths=result)
    assert rc == 1, (
        f"pull request with changed_paths={result} must fail the summary; got rc=0\n{out}"
    )


def test_non_frontend_pr_still_passes_with_e2e_skipped():
    """The PR path is deliberately path-gated: a backend-only PR skips the SPA
    suite by design and must not be blocked by this change."""
    rc, _ = _run(event="pull_request", ref="refs/heads/topic",
                 fast="success", build="success", integration="skipped",
                 e2e="skipped", is_frontend_pr="false")
    assert rc == 0


@pytest.mark.parametrize("result", ["failure", "skipped", "cancelled"])
def test_concurrency_pr_requires_e2e_success(result):
    rc, out = _run(
        event="pull_request",
        ref="refs/heads/topic",
        fast="success",
        build="success",
        integration="success",
        e2e=result,
        is_concurrency_pr="true",
    )
    assert rc == 1, (
        f"concurrency PR with e2e={result} must fail the summary; got rc=0\n{out}"
    )
    assert result in out
