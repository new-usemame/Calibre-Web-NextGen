"""Unit tests for scripts/lib/tier_policy.py — the shared policy module
used by the auto-merge workflow and the autopilot triage script.

These tests pin the classification + validation contract. If they
regress, both consumers regress together; the operator should treat any
red here as a merge-gate-breaker.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# Add repo root to sys.path so `from scripts.lib import tier_policy` works.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.lib import tier_policy  # noqa: E402

POLICY_PATH = REPO_ROOT / ".github" / "policy" / "tier-policy.config"

pytestmark = pytest.mark.unit


# ─── load_policy ───────────────────────────────────────────────────────


def test_load_policy_reads_canonical_config():
    p = tier_policy.load_policy()
    assert p.tier2_max_additions > 0
    assert p.tier2_max_files > 0
    assert p.tier1_paths_regex.search("cps/translations/de/LC_MESSAGES/messages.po")
    assert p.forbidden_paths_regex.search("cps/oauth.py")
    assert "validate-author" in p.tier1_required_checks
    assert "Integration Tests (Docker)" in p.tier2_required_checks


def test_load_policy_accepts_explicit_path(tmp_path):
    cfg = tmp_path / "tp.conf"
    cfg.write_text(textwrap.dedent("""
        TIER1_PATHS_REGEX='\\.foo$'
        TIER2_MAX_ADDITIONS=7
        TIER2_MAX_FILES=1
        FORBIDDEN_PATHS_REGEX='unique-sentinel'
        FORBIDDEN_DIFF_CONTENT_REGEX='unique-diff'
        TIER1_REQUIRED_CHECKS='check-A'
        TIER2_REQUIRED_CHECKS='check-A,check-B'
    """).lstrip())
    p = tier_policy.load_policy(cfg)
    assert p.tier2_max_additions == 7
    assert p.tier2_max_files == 1
    assert p.forbidden_paths_regex.pattern == "unique-sentinel"
    assert p.tier2_required_checks == ("check-A", "check-B")


def test_load_policy_respects_env_var(tmp_path, monkeypatch):
    cfg = tmp_path / "env.conf"
    cfg.write_text("TIER2_MAX_ADDITIONS=99\nTIER2_MAX_FILES=2\n")
    monkeypatch.setenv("CWNG_TIER_POLICY_CONFIG", str(cfg))
    p = tier_policy.load_policy()
    assert p.tier2_max_additions == 99


def test_load_policy_falls_back_when_file_missing(capsys):
    p = tier_policy.load_policy("/tmp/definitely-not-a-file.config")
    # Historical defaults still produce a usable policy so triage doesn't
    # crash during the rollout commit's own CI run on stale main.
    assert p.tier2_max_additions > 0
    captured = capsys.readouterr()
    assert "tier-policy.config" in captured.err


# ─── classify_upstream_pr ──────────────────────────────────────────────


@pytest.fixture
def policy():
    return tier_policy.load_policy()


def test_classify_translation_only_is_safe_merge(policy):
    pr = {
        "additions": 200, "deletions": 50, "changedFiles": 3,
        "files": [
            {"path": "cps/translations/de/LC_MESSAGES/messages.po"},
            {"path": "cps/translations/fr/LC_MESSAGES/messages.po"},
        ],
    }
    bucket, reason = tier_policy.classify_upstream_pr(pr, policy)
    assert bucket == "safe-merge"
    assert "i18n" in reason


def test_classify_forbidden_path_is_review_merge(policy):
    pr = {
        "additions": 5, "deletions": 5, "changedFiles": 1,
        "files": [{"path": "cps/oauth.py"}],
    }
    bucket, reason = tier_policy.classify_upstream_pr(pr, policy)
    assert bucket == "review-merge"
    assert "forbidden" in reason


def test_classify_large_file_count_is_complex(policy):
    pr = {
        "additions": 300, "deletions": 100, "changedFiles": 12,
        "files": [{"path": f"cps/helpers/h{i}.py"} for i in range(12)],
    }
    bucket, _ = tier_policy.classify_upstream_pr(pr, policy)
    assert bucket == "complex"


def test_classify_large_loc_is_complex(policy):
    pr = {
        "additions": 400, "deletions": 200, "changedFiles": 3,
        "files": [{"path": f"cps/helpers/h{i}.py"} for i in range(3)],
    }
    bucket, _ = tier_policy.classify_upstream_pr(pr, policy)
    assert bucket == "complex"


def test_classify_dep_change_is_defer(policy):
    # pyproject.toml is NOT in FORBIDDEN_PATHS_REGEX so the deps arm wins.
    pr = {
        "additions": 5, "deletions": 1, "changedFiles": 1,
        "files": [{"path": "pyproject.toml"}],
    }
    bucket, _ = tier_policy.classify_upstream_pr(pr, policy)
    assert bucket == "defer"


def test_classify_small_isolated_is_safe_merge(policy):
    pr = {
        "additions": 12, "deletions": 3, "changedFiles": 1,
        "files": [{"path": "cps/helpers/foo.py"}],
    }
    bucket, reason = tier_policy.classify_upstream_pr(pr, policy)
    assert bucket == "safe-merge"
    assert "small isolated" in reason


def test_classify_medium_is_review_merge(policy):
    # Above tier-2 caps, below the complex threshold.
    n_lines = policy.tier2_max_additions + 50
    pr = {
        "additions": n_lines, "deletions": 5, "changedFiles": 2,
        "files": [{"path": "cps/helpers/a.py"}, {"path": "cps/helpers/b.py"}],
    }
    bucket, _ = tier_policy.classify_upstream_pr(pr, policy)
    assert bucket == "review-merge"


# ─── validate_fork_pr ──────────────────────────────────────────────────


def test_validate_rejects_unknown_tier(policy):
    r = tier_policy.validate_fork_pr({}, "", policy, tier="bogus")
    assert not r.ok
    assert r.category == "input_error"


def test_validate_tier1_translation_passes(policy):
    pr = {
        "additions": 600, "changedFiles": 5, "baseRefName": "main",
        "files": [{"path": "cps/translations/de/LC_MESSAGES/messages.po"}],
    }
    r = tier_policy.validate_fork_pr(pr, '+ msgstr "hallo"\n', policy, tier="safe-tier-1")
    assert r.ok


def test_validate_forbidden_path_demotes(policy):
    pr = {
        "additions": 1, "changedFiles": 1, "baseRefName": "main",
        "files": [{"path": "requirements.txt"}],
    }
    r = tier_policy.validate_fork_pr(pr, "+flask==1.0\n", policy, tier="safe-tier-1")
    assert not r.ok
    assert r.category == "forbidden_path"


def test_validate_tier2_loc_cap_demotes(policy):
    over = policy.tier2_max_additions + 10
    pr = {
        "additions": over, "changedFiles": 1, "baseRefName": "main",
        "files": [{"path": "cps/helpers/foo.py"}],
    }
    r = tier_policy.validate_fork_pr(pr, "+x = 1\n", policy, tier="safe-tier-2")
    assert not r.ok
    assert r.category == "tier_caps"


def test_validate_tier2_file_cap_demotes(policy):
    n = policy.tier2_max_files + 1
    files = [{"path": f"cps/helpers/h{i}.py"} for i in range(n)]
    pr = {"additions": 5, "changedFiles": n, "baseRefName": "main", "files": files}
    r = tier_policy.validate_fork_pr(pr, "+x = 1\n", policy, tier="safe-tier-2")
    assert not r.ok
    assert r.category == "tier_caps"


def test_validate_diff_content_demotes(policy):
    pr = {
        "additions": 5, "changedFiles": 1, "baseRefName": "main",
        "files": [{"path": "cps/helpers/foo.py"}],
    }
    diff = (
        "diff --git a/foo b/foo\n"
        "+++ b/cps/helpers/foo.py\n"
        "+app.secret_key = 'hunter2'\n"
    )
    r = tier_policy.validate_fork_pr(pr, diff, policy, tier="safe-tier-2")
    assert not r.ok
    assert r.category == "forbidden_diff"
    assert "sensitive" in r.reason


def test_validate_ignores_diff_header_lines(policy):
    pr = {
        "additions": 5, "changedFiles": 1, "baseRefName": "main",
        "files": [{"path": "cps/helpers/foo.py"}],
    }
    # '+++ b/cps/auth/forms.py' is a header, not added content.
    diff = "+++ b/cps/auth/forms.py\n+ y = 1\n"
    r = tier_policy.validate_fork_pr(pr, diff, policy, tier="safe-tier-2")
    assert r.ok


# ─── CLI smoke ─────────────────────────────────────────────────────────


def _run_module(*args, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "scripts.lib.tier_policy", *args],
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_cli_classify_emits_tsv(tmp_path):
    pr_json = tmp_path / "pr.json"
    pr_json.write_text(json.dumps({
        "additions": 5, "deletions": 1, "changedFiles": 1,
        "files": [{"path": "cps/helpers/foo.py"}],
    }))
    cp = _run_module("classify-upstream-pr", str(pr_json))
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout.startswith("safe-merge\t")


def test_cli_validate_emits_json(tmp_path):
    pr_json = tmp_path / "pr.json"
    diff = tmp_path / "diff.txt"
    pr_json.write_text(json.dumps({
        "additions": 5, "changedFiles": 1, "baseRefName": "main",
        "files": [{"path": "cps/helpers/foo.py"}],
    }))
    diff.write_text("+x = 1\n")
    cp = _run_module(
        "validate-fork-pr", "--tier", "safe-tier-2",
        str(pr_json), str(diff),
    )
    assert cp.returncode == 0
    out = json.loads(cp.stdout)
    assert out["ok"] is True


def test_cli_validate_forbidden_returns_ok_false(tmp_path):
    pr_json = tmp_path / "pr.json"
    diff = tmp_path / "diff.txt"
    pr_json.write_text(json.dumps({
        "additions": 1, "changedFiles": 1, "baseRefName": "main",
        "files": [{"path": "requirements.txt"}],
    }))
    diff.write_text("+flask==1.0\n")
    cp = _run_module(
        "validate-fork-pr", "--tier", "safe-tier-1",
        str(pr_json), str(diff),
    )
    # Exit 0 always; the JSON's `ok` field is the contract.
    assert cp.returncode == 0, cp.stderr
    out = json.loads(cp.stdout)
    assert out["ok"] is False
    assert out["category"] == "forbidden_path"


def test_cli_dump_policy_emits_json():
    cp = _run_module("dump-policy")
    assert cp.returncode == 0
    out = json.loads(cp.stdout)
    for key in (
        "tier2_max_additions", "tier2_max_files",
        "tier1_required_checks", "tier2_required_checks",
        "forbidden_paths_regex", "forbidden_diff_content_regex",
    ):
        assert key in out
