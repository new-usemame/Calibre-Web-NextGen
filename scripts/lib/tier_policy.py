"""Shared tier-policy logic for CWNG autopilot.

Single source of truth for two consumers:
  - scripts/triage-prs.sh — upstream-PR classification into buckets
  - .github/workflows/auto-merge.yml — fork-PR merge gate

Both read .github/policy/tier-policy.config (key=value, shell-sourceable) and
both need the same regex/cap evaluation. Keeping the logic in one place means
the workflow can't drift from the triage script in a way that opens a hole.

Usage as a library:
    from scripts.lib import tier_policy
    policy = tier_policy.load_policy()
    bucket, reason = tier_policy.classify_upstream_pr(pr_metadata, policy)
    result = tier_policy.validate_fork_pr(pr_metadata, diff_text, policy)

Usage as a CLI:
    python -m scripts.lib.tier_policy classify-upstream-pr <pr.json>
    python -m scripts.lib.tier_policy validate-fork-pr <pr.json> <diff.txt>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Default config location relative to the repo root. This module lives at
# `scripts/lib/tier_policy.py` inside the repo, so the config sits two
# levels up. Callers from outside the repo (the autopilot's
# scripts/triage-prs.sh) can pass an explicit path or set
# CWNG_TIER_POLICY_CONFIG.
DEFAULT_CONFIG_RELPATH = Path(".github/policy/tier-policy.config")

# Historical defaults used only if tier-policy.config is missing (the case
# during the rollout commit's own CI run on stale main, or in test fixtures
# that don't ship the file). Mirrors the fallback in triage-prs.sh prior
# to the module extraction.
_HISTORICAL_DEFAULTS = {
    "TIER1_PATHS_REGEX": r"\.(po|pot|md)$|^README",
    "TIER2_MAX_ADDITIONS": "50",
    "TIER2_MAX_FILES": "3",
    "FORBIDDEN_PATHS_REGEX": r"(auth|login|csrf|oauth|admin|permission|session)",
    "FORBIDDEN_DIFF_CONTENT_REGEX": (
        r"\b(Authorization\s*[:=]|app\.secret_key|csrf\.exempt|"
        r"@csrf_exempt|secret_key|alembic|eval\s*\(|exec\s*\()"
    ),
    # EMPTY on purpose, unlike every other key here. The rest of this table
    # reproduces the historical behaviour when the config file is absent;
    # doing that for the base allowlist would mean synthesising an
    # authorisation nobody wrote down. A missing or misspelled key would
    # then silently authorise `main` on the strength of a default, and the
    # claim "main is protected" is an external fact about the ruleset that
    # this file cannot check. Empty means every base is refused, which is
    # the safe direction for an unattended-merge authorisation.
    "AUTOMERGE_ALLOWED_BASE_BRANCHES": "",
    "TIER1_REQUIRED_CHECKS": "validate-author,Fast Tests (Smoke + Unit)",
    "TIER2_REQUIRED_CHECKS": (
        "validate-author,Fast Tests (Smoke + Unit),Integration Tests (Docker)"
    ),
}

# Dependency-file heuristic for upstream-PR triage. Kept in code rather than
# the config because it's a triage-only signal (it gates the 'defer' bucket
# decision); the merge gate uses FORBIDDEN_PATHS_REGEX which already covers
# requirements/package.json paths.
_DEP_PATH_TOKENS = (
    "requirements",
    "package.json",
    "package-lock",
    "pyproject",
    "pipfile",
    "gemfile",
    "cargo",
)


@dataclass(frozen=True)
class Policy:
    tier1_paths_regex: re.Pattern[str]
    tier2_max_additions: int
    tier2_max_files: int
    forbidden_paths_regex: re.Pattern[str]
    forbidden_diff_content_regex: re.Pattern[str]
    tier1_required_checks: tuple[str, ...]
    tier2_required_checks: tuple[str, ...]
    automerge_allowed_base_branches: tuple[str, ...]
    raw: dict[str, str]


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str = ""
    # forbidden_path | forbidden_diff | tier_caps | unprotected_base | ok
    category: str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "category": self.category}


# ─── Loading ───────────────────────────────────────────────────────────

# Parser regex pinned by test_tier_policy_documented_python_parser_matches_every_key.
# Matches shell KEY='value' or KEY=value lines; quotes optional.
_KEY_LINE_RE = re.compile(r"^([A-Z0-9_]+)='?(.*?)'?$")


def _resolve_default_config() -> Path:
    """Walk up from this file looking for `.github/policy/tier-policy.config`.

    Handles three deployment shapes:
      - CI checkout: module at <repo>/scripts/lib/tier_policy.py, config at
        <repo>/.github/policy/tier-policy.config (2 levels up).
      - Autopilot local: module at <proj>/repo/scripts/lib/tier_policy.py,
        same relative resolution from the module file.
      - Editable install / weird working dirs: walk up as a fallback.
    """
    here = Path(__file__).resolve().parent
    for ancestor in [here, *here.parents]:
        candidate = ancestor / DEFAULT_CONFIG_RELPATH
        if candidate.exists():
            return candidate
    # Couldn't find it; return the most likely path so the warning message
    # is useful.
    return here.parents[1] / DEFAULT_CONFIG_RELPATH


def _read_config_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with path.open() as fh:
        for line in fh:
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            m = _KEY_LINE_RE.match(line.rstrip("\n"))
            if m:
                out[m.group(1)] = m.group(2)
    return out


def load_policy(config_path: str | Path | None = None) -> Policy:
    """Load tier-policy.config into a Policy.

    config_path is resolved as:
      1. Explicit argument if given.
      2. $CWNG_TIER_POLICY_CONFIG if set (CI / test override).
      3. Walk up from this module's location until a directory containing
         `.github/policy/tier-policy.config` is found. Works whether the
         module is imported from the repo root (CI), from a checkout
         under autopilot's `repo/` subdir, or from a worktree.
      4. Historical defaults (the file is missing) — emit a warning to stderr.
    """
    if config_path is not None:
        cfg_path = Path(config_path)
    elif os.environ.get("CWNG_TIER_POLICY_CONFIG"):
        cfg_path = Path(os.environ["CWNG_TIER_POLICY_CONFIG"])
    else:
        cfg_path = _resolve_default_config()

    if cfg_path.exists():
        raw = _read_config_file(cfg_path)
    else:
        print(
            f"warn: tier-policy.config not at {cfg_path}; falling back to historical defaults",
            file=sys.stderr,
        )
        raw = {}

    merged = {**_HISTORICAL_DEFAULTS, **raw}

    return Policy(
        tier1_paths_regex=re.compile(merged["TIER1_PATHS_REGEX"]),
        tier2_max_additions=int(merged["TIER2_MAX_ADDITIONS"]),
        tier2_max_files=int(merged["TIER2_MAX_FILES"]),
        forbidden_paths_regex=re.compile(merged["FORBIDDEN_PATHS_REGEX"]),
        forbidden_diff_content_regex=re.compile(merged["FORBIDDEN_DIFF_CONTENT_REGEX"]),
        tier1_required_checks=tuple(
            s.strip() for s in merged["TIER1_REQUIRED_CHECKS"].split(",") if s.strip()
        ),
        tier2_required_checks=tuple(
            s.strip() for s in merged["TIER2_REQUIRED_CHECKS"].split(",") if s.strip()
        ),
        automerge_allowed_base_branches=tuple(
            s.strip()
            for s in merged["AUTOMERGE_ALLOWED_BASE_BRANCHES"].split(",")
            if s.strip()
        ),
        raw=merged,
    )


# ─── Upstream-PR triage ────────────────────────────────────────────────


def _paths_from_pr(pr: dict) -> list[str]:
    return [(f.get("path") or "") for f in (pr.get("files") or [])]


def _is_translation_only(pr: dict) -> bool:
    paths = _paths_from_pr(pr)
    return bool(paths) and all(p.endswith(".po") for p in paths)


def classify_upstream_pr(pr: dict, policy: Policy) -> tuple[str, str]:
    """Return (bucket, reason). Mirrors the bash heuristic_bucket in
    triage-prs.sh. Buckets: safe-merge, review-merge, complex, defer.
    """
    if _is_translation_only(pr):
        return ("safe-merge", "i18n .po only")
    paths = _paths_from_pr(pr)
    if any(policy.forbidden_paths_regex.search(p) for p in paths):
        return (
            "review-merge",
            "touches forbidden path (per tier-policy.config) — manual review required",
        )
    additions = pr.get("additions", 0)
    deletions = pr.get("deletions", 0)
    changed_files = pr.get("changedFiles", 0)
    if changed_files >= 10 or (additions + deletions) > 500:
        return ("complex", "large surface area")
    paths_lower = [p.lower() for p in paths]
    if any(any(tok in p for tok in _DEP_PATH_TOKENS) for p in paths_lower):
        return ("defer", "modifies dependencies — needs supply-chain review")
    if changed_files <= policy.tier2_max_files and additions <= policy.tier2_max_additions:
        return (
            "safe-merge",
            f"small isolated change (≤{policy.tier2_max_additions} additions, "
            f"≤{policy.tier2_max_files} files)",
        )
    return ("review-merge", "medium change — needs reading")


# ─── Fork-PR merge gate ────────────────────────────────────────────────


def _added_lines(diff: str) -> Iterable[str]:
    """Yield lines starting with '+' but not the '+++' header. Mirrors the
    `grep -E '^\\+[^+]'` filter in auto-merge.yml.
    """
    for line in diff.splitlines():
        if not line.startswith("+"):
            continue
        if line.startswith("+++"):
            continue
        yield line


def validate_fork_pr(
    pr: dict,
    diff: str,
    policy: Policy,
    *,
    tier: str,
) -> ValidationResult:
    """Run the merge-gate checks that auto-merge.yml performs.

    `tier` must be 'safe-tier-1' or 'safe-tier-2'; the caller has already
    confirmed the label is present and (per PR B's label-guard contract)
    was applied by a trusted author.

    Returns ValidationResult with ok=False and a category whenever a check
    fails. Caller is expected to demote to needs-review on failure.
    """
    if tier not in ("safe-tier-1", "safe-tier-2"):
        return ValidationResult(
            ok=False,
            reason=f"unrecognized tier '{tier}'",
            category="input_error",
        )

    # 0. Base branch must be one the required-status-checks ruleset covers.
    #
    # This runs first because it is decisive on its own, and because it is
    # the assumption every later check silently rests on. auto-merge.yml
    # does not poll required checks; it arms `gh pr merge --auto` and lets
    # branch protection hold the line. On a base the ruleset does not
    # cover, the required-check list is EMPTY — and GitHub treats an empty
    # list as satisfied, so the PR merges immediately with nothing having
    # run. tests.yml does not even fire there (its pull_request trigger is
    # filtered to [main, dev]), so the checks are absent, not red.
    #
    # Fail closed on a missing/blank base: if we cannot prove the base is
    # protected, we must not arm. An absent ruleset is not a passing one.
    base = (pr.get("baseRefName") or "").strip()
    allowed = policy.automerge_allowed_base_branches
    if not allowed:
        return ValidationResult(
            ok=False,
            reason=(
                "AUTOMERGE_ALLOWED_BASE_BRANCHES is empty or missing from "
                "tier-policy.config, so no base is authorised for auto-merge. "
                "Refusing rather than assuming a default."
            ),
            category="unprotected_base",
        )
    if not base:
        return ValidationResult(
            ok=False,
            reason=(
                "cannot determine the PR's base branch, so cannot confirm it is "
                "covered by branch protection; refusing to arm auto-merge."
            ),
            category="unprotected_base",
        )
    if base not in allowed:
        return ValidationResult(
            ok=False,
            reason=(
                f"base branch `{base}` is not covered by the required-status-checks "
                f"ruleset (covered: {', '.join(allowed)}). Auto-merge delegates check "
                "enforcement to branch protection, so arming here would merge with no "
                "required checks at all. Re-target this PR at a protected base once "
                "the branch it stacks on has landed."
            ),
            category="unprotected_base",
        )

    paths = _paths_from_pr(pr)

    # 1. Forbidden paths (filename).
    forbidden_hits = [p for p in paths if policy.forbidden_paths_regex.search(p)]
    if forbidden_hits:
        return ValidationResult(
            ok=False,
            reason=(
                "PR touches forbidden paths "
                "(auth/csrf/session/admin/migrations/deps/container); "
                "demoted to needs-review."
            ),
            category="forbidden_path",
        )

    # 2. Tier-2 size caps. Tier-1 has no LOC cap because translations can
    # legitimately span many files / many lines.
    if tier == "safe-tier-2":
        additions = pr.get("additions", 0)
        file_count = len(paths) if paths else pr.get("changedFiles", 0)
        if (
            additions > policy.tier2_max_additions
            or file_count > policy.tier2_max_files
        ):
            return ValidationResult(
                ok=False,
                reason=(
                    f"PR is too large for safe-tier-2 (additions={additions}, "
                    f"files={file_count}; caps are {policy.tier2_max_additions} LOC / "
                    f"{policy.tier2_max_files} files). Demoted to needs-review."
                ),
                category="tier_caps",
            )

    # 3. Diff-content scan — sensitive tokens added in allowed paths still
    # demote. Run after the path check so we get the more-specific reason
    # when both fire.
    sensitive_hits: list[str] = []
    for line in _added_lines(diff):
        for m in policy.forbidden_diff_content_regex.finditer(line):
            sensitive_hits.append(m.group(0))
    if sensitive_hits:
        # Dedup + cap to keep the comment short.
        unique = list(dict.fromkeys(sensitive_hits))[:3]
        joined = ", ".join(f"`{h}`" for h in unique)
        return ValidationResult(
            ok=False,
            reason=(
                f"PR diff contains sensitive token(s) — {joined} — "
                "demoted to needs-review for human eyes."
            ),
            category="forbidden_diff",
        )

    return ValidationResult(ok=True, category="ok")


# ─── CLI ───────────────────────────────────────────────────────────────


def _load_pr(path: str) -> dict:
    if path == "-":
        return json.load(sys.stdin)
    with open(path) as fh:
        return json.load(fh)


def _load_diff(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path) as fh:
        return fh.read()


def _cmd_classify(args: argparse.Namespace) -> int:
    policy = load_policy(args.config)
    pr = _load_pr(args.pr_json)
    bucket, reason = classify_upstream_pr(pr, policy)
    if args.json:
        json.dump({"bucket": bucket, "reason": reason}, sys.stdout)
        sys.stdout.write("\n")
    else:
        print(f"{bucket}\t{reason}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    policy = load_policy(args.config)
    pr = _load_pr(args.pr_json)
    diff = _load_diff(args.diff)
    result = validate_fork_pr(pr, diff, policy, tier=args.tier)
    json.dump(result.to_dict(), sys.stdout)
    sys.stdout.write("\n")
    # Exit 0 always; the JSON's `ok` field is the contract. Bash callers
    # parse with jq -r .ok and decide. This avoids the bash trap where a
    # non-zero exit makes `set -e` kill the loop before we can comment.
    return 0


def _cmd_dump(args: argparse.Namespace) -> int:
    """Emit the resolved policy as JSON. Lets bash callers grab e.g. the
    required-check list without re-parsing the config themselves.
    """
    policy = load_policy(args.config)
    out = {
        "tier2_max_additions": policy.tier2_max_additions,
        "tier2_max_files": policy.tier2_max_files,
        "tier1_required_checks": list(policy.tier1_required_checks),
        "tier2_required_checks": list(policy.tier2_required_checks),
        "automerge_allowed_base_branches": list(policy.automerge_allowed_base_branches),
        "tier1_paths_regex": policy.tier1_paths_regex.pattern,
        "forbidden_paths_regex": policy.forbidden_paths_regex.pattern,
        "forbidden_diff_content_regex": policy.forbidden_diff_content_regex.pattern,
    }
    json.dump(out, sys.stdout, indent=2 if args.pretty else None)
    sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tier_policy", description=__doc__)
    parser.add_argument(
        "--config",
        help="path to tier-policy.config (default: $CWNG_TIER_POLICY_CONFIG "
        "or <proj>/repo/.github/policy/tier-policy.config)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_cls = sub.add_parser(
        "classify-upstream-pr",
        help="bucket an upstream PR (safe-merge|review-merge|complex|defer)",
    )
    p_cls.add_argument("pr_json", help="path to PR JSON or '-' for stdin")
    p_cls.add_argument("--json", action="store_true", help="emit JSON not TSV")
    p_cls.set_defaults(func=_cmd_classify)

    p_val = sub.add_parser(
        "validate-fork-pr",
        help="run the merge-gate checks on a fork-PR (forbidden paths/diff, tier caps)",
    )
    p_val.add_argument("pr_json", help="path to PR JSON (with .files / .additions) or '-'")
    p_val.add_argument("diff", help="path to PR diff text or '-'")
    p_val.add_argument(
        "--tier",
        required=True,
        choices=["safe-tier-1", "safe-tier-2"],
        help="which tier the PR carries (caller already verified the label)",
    )
    p_val.set_defaults(func=_cmd_validate)

    p_dump = sub.add_parser(
        "dump-policy",
        help="emit the resolved policy values as JSON (for bash consumers)",
    )
    p_dump.add_argument("--pretty", action="store_true")
    p_dump.set_defaults(func=_cmd_dump)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
