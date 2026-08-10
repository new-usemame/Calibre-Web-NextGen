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


def _live_lines() -> list[str]:
    """auto-merge.yml lines with comments and blanks removed.

    Text assertions over a workflow are only worth anything if a commented-out
    or dead line cannot satisfy them — this file's own prose explains the gate
    at length, so `#`-lines mention every token these tests look for.
    """
    out = []
    for ln in AUTO_MERGE_WF.read_text().splitlines():
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(ln)
    return out


def _unprotected_base_branch_body() -> str:
    """The executable body of the `unprotected_base` handler.

    Sliced from the `if` that tests the category to its closing `fi` at the
    same indent, so an assertion about this branch cannot be satisfied by
    something elsewhere in the workflow.
    """
    lines = _live_lines()
    start = None
    for i, ln in enumerate(lines):
        if 'category" = "unprotected_base"' in ln:
            start = i
            break
    assert start is not None, "no `unprotected_base` category branch found"
    indent = len(lines[start]) - len(lines[start].lstrip())
    body = [lines[start]]
    for ln in lines[start + 1:]:
        body.append(ln)
        if ln.strip() == "fi" and (len(ln) - len(ln.lstrip())) == indent:
            break
    return "\n".join(body)


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


def test_policy_exposes_exactly_the_default_branch(policy):
    """Exact equality, not membership. `"main" in (...)` would happily accept
    an allowlist that had also picked up `dev` or a feature branch."""
    assert policy.automerge_allowed_base_branches == ("main",)


def test_canonical_config_declares_the_key():
    """Parse the value rather than grep for the name — a commented-out line
    mentioning the key would satisfy a substring check while leaving the
    allowlist empty."""
    parsed = tier_policy.load_policy(POLICY_PATH)
    assert parsed.automerge_allowed_base_branches == ("main",)
    assert parsed.raw.get("AUTOMERGE_ALLOWED_BASE_BRANCHES") == "main"


def test_historical_defaults_authorise_nothing():
    """load_policy() falls back to _HISTORICAL_DEFAULTS when the config file
    is missing, and merges them under a config that omits the key.

    Every other default here reproduces historical behaviour. This one must
    not: defaulting to "main" would synthesise an authorisation nobody wrote
    down, on the strength of an external fact (that main is the protected
    default) which this module cannot verify."""
    assert tier_policy._HISTORICAL_DEFAULTS["AUTOMERGE_ALLOWED_BASE_BRANCHES"] == ""


def test_missing_config_key_refuses_every_base(tmp_path):
    """The end-to-end consequence of the empty default: delete or misspell
    the key and auto-merge stops, rather than quietly falling back."""
    cfg = tmp_path / "tier-policy.config"
    cfg.write_text(
        "TIER1_PATHS_REGEX='\\.(po|pot|md)$|^README'\n"
        "TIER2_MAX_ADDITIONS=80\n"
        "TIER2_MAX_FILES=3\n"
        # AUTOMERGE_ALLOWED_BASE_BRANCHES deliberately absent
    )
    p = tier_policy.load_policy(cfg)
    assert p.automerge_allowed_base_branches == ()
    r = tier_policy.validate_fork_pr(
        _clean_tier1_pr(baseRefName="main"), CLEAN_DIFF, p, tier="safe-tier-1"
    )
    assert not r.ok, "an absent allowlist must refuse even the default branch"
    assert r.category == "unprotected_base"


# ─── near-miss base names ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "base",
    [
        "Main", "MAIN", "mAin",          # case
        "main-old", "old-main", "mainx",  # suffix / prefix
        "main/foo", "foo/main",           # path-ish
        "refs/heads/main",                # fully-qualified ref
        "ma\u0456n",                      # Cyrillic i lookalike
        " main\t",                        # only whitespace-trimmed forms pass
    ],
)
def test_near_miss_base_names_are_refused(policy, base):
    """The comparison is exact and case-sensitive. `refs/heads/main` is in the
    list because `baseRefName` is the short name — if a caller ever hands us
    the long form, refusing is the right answer, not silently matching."""
    r = tier_policy.validate_fork_pr(
        _clean_tier1_pr(baseRefName=base), CLEAN_DIFF, policy, tier="safe-tier-1"
    )
    if base.strip() == "main":
        assert r.ok, "surrounding whitespace is stripped; branch names cannot contain it"
    else:
        assert not r.ok, f"{base!r} must not be treated as the default branch"
        assert r.category == "unprotected_base"


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
    view_lines = [
        ln for ln in _live_lines()
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
    body = _unprotected_base_branch_body()
    assert "--disable-auto" in body, (
        "the unprotected_base path must call `gh pr merge --disable-auto`; "
        f"declining to arm leaves an existing sticky arm in force. Body:\n{body}"
    )


def test_workflow_does_not_strip_tier_labels_for_an_unprotected_base():
    """A stacked PR's tier label is not wrong — its base is. Stripping the
    label would thrash against the autopilot, which re-applies it.

    Asserted against the branch BODY, not the whole file: `unprotected_base`
    appearing somewhere in the workflow is satisfied by a comment, or by a
    handler that then falls through to the generic demote-and-strip path.
    """
    body = _unprotected_base_branch_body()
    assert "--remove-label" not in body, (
        "the unprotected_base path must not demote the PR; found a label strip "
        f"in its body:\n{body}"
    )
    assert "exit 0" in body, (
        "the unprotected_base path must stop before the arming step"
    )


def test_unprotected_base_path_never_reaches_the_arming_command():
    """The base=main positive control proves the validator still says yes; it
    cannot prove the workflow still reaches `gh pr merge --auto`. This pins the
    other side — the refusal path must not fall through into arming, and the
    arming command must still exist for PRs that pass."""
    body = _unprotected_base_branch_body()
    assert "--auto" not in body, (
        "the unprotected_base path must not reach the arming command"
    )
    text = AUTO_MERGE_WF.read_text()
    assert "--auto --squash" in text, (
        "the workflow must still arm auto-merge for a PR that passes the gate"
    )
