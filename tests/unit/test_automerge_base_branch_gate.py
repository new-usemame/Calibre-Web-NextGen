# SPDX-License-Identifier: GPL-3.0-or-later
"""The auto-merge gate must refuse to arm on a base branch that nothing protects.

Why this file exists
--------------------
`auto-merge.yml` deliberately does NOT poll required status checks. Its own
comment says so: *"We do NOT poll required checks here. GitHub waits for them
via branch protection."* That delegation is sound only while the PR's base is
a ref the required-status-checks ruleset actually covers.

It isn't, for a stacked PR. The `main protection` ruleset is scoped to
`~DEFAULT_BRANCH`, so a PR based on a feature branch is outside it entirely,
and `tests.yml` (`pull_request: branches: [main, dev]`) never even runs — the
checks are absent rather than red. Arming `gh pr merge --auto --squash` there
hands GitHub an empty required-check list, and an empty list is satisfied
immediately: the PR merges with no test having run.

Observed 2026-08-10: #1509 (base `feat/reader-notes`) and #1514 (base
`feat/reader-annotations-panel`) sat open with only `evaluate` reported. Both
were one tier label away from merging untested.

Same family as `an absent required check is not a passing one` (F-436d41) —
here it is an absent *ruleset* rather than an absent check.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.lib import tier_policy  # noqa: E402

POLICY_PATH = REPO_ROOT / ".github" / "policy" / "tier-policy.config"
AUTO_MERGE_WF = REPO_ROOT / ".github" / "workflows" / "auto-merge.yml"

pytestmark = pytest.mark.unit


@pytest.fixture
def policy():
    return tier_policy.load_policy(POLICY_PATH)


def _clean_tier1_pr(**overrides) -> dict:
    """A PR that passes every OTHER tier-1 gate.

    Translation-only, so no forbidden path, no size cap, no sensitive diff.
    Anything this fixture fails on is the base-branch gate and nothing else.
    """
    pr = {
        "additions": 12,
        "changedFiles": 1,
        "baseRefName": "main",
        "files": [{"path": "cps/translations/de/LC_MESSAGES/messages.po"}],
    }
    pr.update(overrides)
    return pr


CLEAN_DIFF = '+ msgstr "hallo"\n'


# ─── the gate itself ───────────────────────────────────────────────────


def test_default_branch_base_is_allowed(policy):
    """Positive control. Without this, a gate that refuses everything would
    look identical to a gate that works."""
    r = tier_policy.validate_fork_pr(
        _clean_tier1_pr(baseRefName="main"), CLEAN_DIFF, policy, tier="safe-tier-1"
    )
    assert r.ok, f"base=main must still auto-merge, got: {r.reason}"


def test_stacked_feature_base_is_refused(policy):
    """The live #1509 shape: based on another feature branch."""
    r = tier_policy.validate_fork_pr(
        _clean_tier1_pr(baseRefName="feat/reader-notes"),
        CLEAN_DIFF,
        policy,
        tier="safe-tier-1",
    )
    assert not r.ok, "a PR based on a feature branch must never arm auto-merge"
    assert r.category == "unprotected_base"
    assert "feat/reader-notes" in r.reason


def test_dev_base_is_refused(policy):
    """`dev` gets tests.yml runs but the ruleset covers ~DEFAULT_BRANCH only,
    so nothing *enforces* those runs at merge time. Running != required."""
    r = tier_policy.validate_fork_pr(
        _clean_tier1_pr(baseRefName="dev"), CLEAN_DIFF, policy, tier="safe-tier-1"
    )
    assert not r.ok
    assert r.category == "unprotected_base"


def test_missing_base_fails_closed(policy):
    """If we cannot prove the base is protected, we must refuse. A gate that
    treats 'unknown' as 'fine' is the bug this file exists to prevent."""
    pr = _clean_tier1_pr()
    del pr["baseRefName"]
    r = tier_policy.validate_fork_pr(pr, CLEAN_DIFF, policy, tier="safe-tier-1")
    assert not r.ok, "absent baseRefName must fail closed, not pass"
    assert r.category == "unprotected_base"


def test_empty_base_fails_closed(policy):
    r = tier_policy.validate_fork_pr(
        _clean_tier1_pr(baseRefName=""), CLEAN_DIFF, policy, tier="safe-tier-1"
    )
    assert not r.ok
    assert r.category == "unprotected_base"


def test_base_gate_applies_to_tier2(policy):
    pr = {
        "additions": 5,
        "changedFiles": 1,
        "baseRefName": "feat/some-stack",
        "files": [{"path": "cps/helpers/foo.py"}],
    }
    r = tier_policy.validate_fork_pr(pr, "+x = 1\n", policy, tier="safe-tier-2")
    assert not r.ok
    assert r.category == "unprotected_base"


def test_base_gate_runs_before_other_checks(policy):
    """An unprotected base is decisive on its own — we should not have to
    reason about size or content to know the answer is no."""
    pr = {
        "additions": 99999,
        "changedFiles": 40,
        "baseRefName": "feat/some-stack",
        "files": [{"path": "requirements.txt"}],
    }
    r = tier_policy.validate_fork_pr(pr, "+flask==1.0\n", policy, tier="safe-tier-2")
    assert not r.ok
    assert r.category == "unprotected_base"


# ─── policy plumbing ───────────────────────────────────────────────────


def test_policy_exposes_allowed_base_branches(policy):
    assert hasattr(policy, "automerge_allowed_base_branches")
    assert "main" in policy.automerge_allowed_base_branches


def test_canonical_config_declares_the_key():
    """The value is policy, so it belongs in the config SSOT, not in code."""
    text = POLICY_PATH.read_text()
    assert "AUTOMERGE_ALLOWED_BASE_BRANCHES" in text


def test_historical_defaults_are_also_closed():
    """load_policy() falls back to _HISTORICAL_DEFAULTS when the config file
    is missing. That path must not be the wide-open one."""
    allowed = tier_policy._HISTORICAL_DEFAULTS["AUTOMERGE_ALLOWED_BASE_BRANCHES"]
    branches = [b.strip() for b in allowed.split(",") if b.strip()]
    assert branches == ["main"]


def test_dump_policy_reports_allowed_bases():
    """Bash consumers read the policy through `dump-policy`; a key it can't
    see is a key it can't honour."""
    out = subprocess.run(
        [sys.executable, "-m", "scripts.lib.tier_policy", "dump-policy"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(out.stdout)
    assert "automerge_allowed_base_branches" in data
    assert data["automerge_allowed_base_branches"] == ["main"]


# ─── the wiring (a gate nothing calls is not a gate) ───────────────────


def test_cli_refuses_stacked_base():
    pr = _clean_tier1_pr(baseRefName="feat/reader-notes")
    with tempfile.TemporaryDirectory() as td:
        pr_path = Path(td) / "pr.json"
        diff_path = Path(td) / "diff.txt"
        pr_path.write_text(json.dumps(pr))
        diff_path.write_text(CLEAN_DIFF)
        out = subprocess.run(
            [
                sys.executable, "-m", "scripts.lib.tier_policy", "validate-fork-pr",
                "--tier", "safe-tier-1", str(pr_path), str(diff_path),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    result = json.loads(out.stdout)
    assert result["ok"] is False
    assert result["category"] == "unprotected_base"


def test_workflow_actually_passes_the_base_to_the_validator():
    """The module can only judge what the workflow hands it. `gh pr view`
    must request baseRefName into the JSON the validator reads, or the gate
    silently degrades to the fail-closed branch on every PR."""
    text = AUTO_MERGE_WF.read_text()
    view_lines = [
        ln for ln in text.splitlines()
        if "gh pr view" in ln and "--json" in ln and "$pr_json" in ln
    ]
    assert view_lines, "expected the validator's `gh pr view ... > $pr_json` line"
    assert any("baseRefName" in ln for ln in view_lines), (
        "auto-merge.yml must fetch baseRefName into the validator's pr.json; "
        f"found: {view_lines}"
    )


def test_workflow_ties_the_base_to_the_actual_default_branch():
    """The allowlist names branches literally; the ruleset is scoped to
    ~DEFAULT_BRANCH. Rename the default branch while a stale `main` survives
    and the two diverge — the allowlist would then authorise a branch nothing
    protects, which is fail-OPEN. The workflow must resolve the real default
    branch and require the base to be it."""
    text = AUTO_MERGE_WF.read_text()
    assert "defaultBranchRef" in text, (
        "auto-merge.yml must resolve the repository's actual default branch "
        "rather than trusting the literal name in the policy allowlist"
    )
    idx = text.find("defaultBranchRef")
    branch = text[max(0, idx - 900):idx + 900]
    assert "unprotected_base" in branch, (
        "a base that is not the default branch must land in the "
        "unprotected_base path"
    )
    # Fail closed: an unresolvable default branch is not permission to arm.
    assert '-z "$default_branch"' in text, (
        "an empty default-branch lookup must refuse, not fall through to arming"
    )


def test_workflow_reevaluates_when_the_base_changes():
    """A PR's base can change after it was armed. `edited` is the event that
    fires for that; without it the gate only ever sees the base a PR was
    opened with."""
    text = AUTO_MERGE_WF.read_text()
    trigger = text.split("jobs:", 1)[0]
    assert "edited" in trigger, (
        "auto-merge.yml must trigger on `edited` so a base change re-runs the "
        "gate; otherwise a PR armed on main and re-targeted at a feature "
        "branch is never re-evaluated"
    )


def test_workflow_disarms_rather_than_only_declining_to_arm():
    """Arming is sticky — scripts/finish-armed-automerges.sh documents a
    months-long bug where a stripped tier label left the arm in place because
    nothing called --disable-auto. Refusing to re-arm is not the same as
    disarming, so the unprotected-base path must do the latter."""
    text = AUTO_MERGE_WF.read_text()
    idx = text.find("unprotected_base")
    assert idx != -1, "expected an unprotected_base branch"
    # Look only at the branch body, not the whole file.
    branch = text[idx:idx + 1800]
    assert "--disable-auto" in branch, (
        "the unprotected_base path must call `gh pr merge --disable-auto`; "
        "declining to arm leaves an existing sticky arm in force"
    )


def test_workflow_does_not_strip_tier_labels_for_an_unprotected_base():
    """A stacked PR's tier label is not wrong — its base is. Stripping the
    label would thrash against the autopilot, which re-applies it."""
    text = AUTO_MERGE_WF.read_text()
    assert "unprotected_base" in text, (
        "auto-merge.yml must special-case the unprotected_base category "
        "instead of routing it through the generic demote-and-strip path"
    )
