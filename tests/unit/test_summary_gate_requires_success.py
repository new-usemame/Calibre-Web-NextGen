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
import re
import subprocess
from pathlib import Path

import pytest

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
            changed_paths="success"):
    subs = {
        "needs.fast-tests.result": fast,
        "needs.frontend-build.result": build,
        "needs.integration-tests.result": integration,
        "needs.e2e-tests.result": e2e,
        "needs.changed_paths.result": changed_paths,
        "github.event_name": event,
        "github.ref": ref,
    }
    for key, value in subs.items():
        shell = shell.replace("${{ " + key + " }}", value)
    # Any residual GitHub expression is a boolean we are not exercising.
    shell = re.sub(r"\$\{\{[^}]*\}\}", "false", shell)
    is_main_push = "true" if (event == "push" and ref == "refs/heads/main") else "false"
    env = (
        f"IS_TIER2_PR={is_tier2}\n"
        f"IS_BUILD_PR={is_build_pr}\n"
        f"IS_FRONTEND_PR={is_frontend_pr}\n"
        f"IS_MAIN_PUSH={is_main_push}\n"
    )
    return "set -o pipefail\n" + env + shell


def _run(**kwargs):
    script = _render(_summary_shell(), **kwargs)
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return proc.returncode, proc.stdout


MAIN_PUSH = dict(event="push", ref="refs/heads/main")


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


def test_non_frontend_pr_still_passes_with_e2e_skipped():
    """The PR path is deliberately path-gated: a backend-only PR skips the SPA
    suite by design and must not be blocked by this change."""
    rc, _ = _run(event="pull_request", ref="refs/heads/topic",
                 fast="success", build="success", integration="skipped",
                 e2e="skipped", is_frontend_pr="false")
    assert rc == 0
