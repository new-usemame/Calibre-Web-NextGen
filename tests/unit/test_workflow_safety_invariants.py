"""CI-enforced safety invariants for .github/workflows/*.yml.

These tests pin the load-bearing properties that keep the auto-merge
machinery safe. They run on every PR (marked `unit`, picked up by
Fast Tests) and go red the moment someone removes one of the walls.

The walls — each tested below:

  1. auto-merge.yml refuses to act on PRs from forks. Without this the
     workflow would try to enable auto-merge / merge for arbitrary
     attacker-controlled branches.

  2. Any workflow that consumes .github/policy/tier-policy.config or
     scripts/lib/tier_policy.py reads it from main, not the PR head.
     Otherwise a PR could widen its own merge rules by editing the
     policy file in its own branch.

  3. No `pull_request_target` workflow checks out the PR head ref or
     PR head SHA. pull_request_target runs in the base repo's
     privileged context with secrets; checking out attacker-controlled
     code there is the classic GitHub Actions RCE.

  4. No workflow grants `permissions: actions: write` (or `write-all`
     at the top level). The auto-merge / label-guard surfaces only
     need pull-requests + contents; broadening permissions is an
     unexplained escalation.

  5. No workflow pushes to or otherwise mutates the upstream repos
     (`crocodilestick/Calibre-Web-Automated`, `janeczku/calibre-web`).
     CLAUDE.md hard rule #2 — never push to upstream.

If any of these go red, STOP. Read the failing assertion. The fix is
almost never to weaken the test; it's to restore the property in the
workflow.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover - yaml ships with most distros
    yaml = None  # type: ignore[assignment]

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WF_DIR = REPO_ROOT / ".github" / "workflows"


def _load(path: Path) -> dict:
    if yaml is None:
        pytest.skip("PyYAML not installed in this environment")
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def _trigger(wf: dict) -> dict:
    """`on:` parsed as either the literal string 'on' or boolean True
    (YAML 1.1 quirk). Return whichever holds."""
    return wf.get("on") or wf.get(True) or {}


def _every_step(wf: dict):
    """Yield (job_name, step_dict) for every step in every job."""
    for job_name, job in (wf.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for step in (job.get("steps") or []):
            if isinstance(step, dict):
                yield job_name, step


def all_workflows() -> list[Path]:
    return sorted(WF_DIR.glob("*.yml")) + sorted(WF_DIR.glob("*.yaml"))


# ─── Wall 1: auto-merge.yml refuses fork PRs ───────────────────────────


def test_auto_merge_refuses_fork_prs():
    """The workflow must bail out for PRs whose head repo isn't the base
    repo. This protects against acting on attacker-controlled branches.
    Two acceptable shapes:
      (a) Job-level `if:` predicate comparing head.repo.full_name to
          github.repository (the new shape).
      (b) Inline shell guard that returns early on the same comparison
          (the legacy shape, still valid).
    """
    f = WF_DIR / "auto-merge.yml"
    assert f.exists(), f"missing {f}"
    text = f.read_text()
    # Either form must reference the head-repo / base-repo comparison.
    assert (
        "head.repo.full_name" in text
    ), "auto-merge.yml must compare head.repo.full_name against github.repository to refuse fork PRs"
    # Pin that the result is a refusal (skip / exit / continue / if:).
    assert re.search(
        r"refus|skip|continue|exit\s+0|fork",
        text,
        re.IGNORECASE,
    ), "auto-merge.yml must visibly refuse/skip on the fork branch"


# ─── Wall 2: policy reads from main, not PR head ───────────────────────


def test_policy_consuming_workflows_checkout_main():
    """Any workflow that sources tier-policy.config or imports
    scripts.lib.tier_policy must use actions/checkout with ref: main,
    not the PR's head ref. Otherwise a PR could ship a poisoned
    policy file and have it consulted at merge time.
    """
    offenders = []
    for path in all_workflows():
        text = path.read_text()
        consumes_policy = (
            "tier-policy.config" in text or "scripts.lib.tier_policy" in text
        )
        if not consumes_policy:
            continue
        # Workflow consumes policy. It must explicitly checkout main.
        # Look for `ref: main` somewhere in the file (a step-level arg).
        if not re.search(r"\bref:\s*main\b", text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        "These workflows consume tier-policy.config but do not "
        f"explicitly checkout ref: main: {offenders}"
    )


# ─── Wall 3: no pull_request_target checks out PR head ─────────────────

_PR_HEAD_REF_PATTERN = re.compile(
    r"github\.event\.pull_request\.head\.(ref|sha)", re.IGNORECASE
)


def test_pull_request_target_never_checks_out_pr_head():
    """pull_request_target runs in the base repo's privileged context.
    Checking out the PR head means running attacker-controlled code
    in that context — a classic GitHub Actions footgun. Pin: no
    pull_request_target workflow references github.event.pull_request.
    head.{ref,sha} anywhere in an actions/checkout step.
    """
    for path in all_workflows():
        wf = _load(path)
        trig = _trigger(wf)
        if "pull_request_target" not in (trig if isinstance(trig, dict) else {}):
            continue
        for job_name, step in _every_step(wf):
            uses = step.get("uses", "")
            if not isinstance(uses, str) or not uses.startswith("actions/checkout"):
                continue
            with_args = step.get("with") or {}
            ref = with_args.get("ref")
            if isinstance(ref, str) and _PR_HEAD_REF_PATTERN.search(ref):
                pytest.fail(
                    f"{path.name}/{job_name}: actions/checkout uses PR head "
                    f"ref ({ref!r}) under pull_request_target — RCE risk"
                )


# ─── Wall 4: no workflow grants permissions: actions: write ────────────

_DANGEROUS_TOP_LEVEL_PERMS = {
    # Mutating other workflows or rewriting branch protection from
    # within an action grants implicit privilege escalation. The
    # tier-1/2 surfaces only ever need pull-requests + contents.
    "actions": "write",
    "deployments": "write",
    "id-token": "write",
}

# Documented exemptions: workflow name → (scope, justification). If
# someone needs to add a new exemption, the entry MUST include the
# reason — the test is the audit log. Format keeps the noise low when
# we eyeball this list.
_DANGEROUS_PERMS_EXEMPTIONS = {
    ("docker-image-build-release.yml", "id-token"): (
        "GitHub OIDC for sigstore/cosign attestations on the container image; "
        "required for SLSA provenance. Issued per-job, expires immediately."
    ),
    ("update-translations.yml", "actions"): (
        "Workflow re-dispatches itself / downstream workflows after pushing "
        "translation updates so dependent runs pick up fresh .po files."
    ),
}


def test_no_workflow_grants_dangerous_permissions():
    """No workflow grants write access to surfaces that aren't required
    for tier-merge. Documented exemptions live in
    _DANGEROUS_PERMS_EXEMPTIONS — adding a new one is the audit
    moment: every exemption must explain why the broader scope is
    necessary. Silent widening goes red.
    """
    offenders = []
    for path in all_workflows():
        wf = _load(path)
        perms = wf.get("permissions")
        if isinstance(perms, str):
            if perms in ("write-all",):
                offenders.append(f"{path.name}: top-level permissions: {perms}")
            continue
        if not isinstance(perms, dict):
            continue
        for scope, level in perms.items():
            if scope not in _DANGEROUS_TOP_LEVEL_PERMS:
                continue
            if level != _DANGEROUS_TOP_LEVEL_PERMS[scope]:
                continue
            if (path.name, scope) in _DANGEROUS_PERMS_EXEMPTIONS:
                continue
            offenders.append(f"{path.name}: permissions.{scope}: {level}")
        # Also check job-level perms.
        for job_name, job in (wf.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            job_perms = job.get("permissions") or {}
            if isinstance(job_perms, str) and job_perms == "write-all":
                offenders.append(f"{path.name}/{job_name}: permissions: {job_perms}")
                continue
            if not isinstance(job_perms, dict):
                continue
            for scope, level in job_perms.items():
                if scope not in _DANGEROUS_TOP_LEVEL_PERMS:
                    continue
                if level != _DANGEROUS_TOP_LEVEL_PERMS[scope]:
                    continue
                if (path.name, scope) in _DANGEROUS_PERMS_EXEMPTIONS:
                    continue
                offenders.append(f"{path.name}/{job_name}: permissions.{scope}: {level}")
    assert not offenders, (
        "Workflows granting dangerous write permissions without explicit exemption "
        f"in _DANGEROUS_PERMS_EXEMPTIONS: {offenders}"
    )


# ─── Wall 5: no workflow pushes / mutates upstream ─────────────────────


def test_no_workflow_pushes_to_upstream():
    """CLAUDE.md hard rule #2: never push to upstream. Workflows
    referencing the upstream repo name are OK only if they're
    read-only (e.g. the integration-test docker image tag uses the
    upstream image name historically). Pin: no `gh ... --repo
    crocodilestick/...` mutating call, no `git push` to upstream.
    """
    upstreams = (
        "crocodilestick/Calibre-Web-Automated",
        "crocodilestick/calibre-web-automated",
        "janeczku/calibre-web",
    )
    offenders = []
    push_re = re.compile(r"\bgit\s+push\b.*({})".format("|".join(upstreams)))
    gh_write_re = re.compile(
        r"\bgh\s+(?:pr|issue|api|release)[^\n]*?\s--repo\s+("
        + "|".join(re.escape(u) for u in upstreams)
        + r")",
        re.IGNORECASE,
    )
    # `gh issue list`, `gh pr list`, `gh api repos/.../issues` (read) are OK.
    # Only flag mutating subcommands.
    gh_mutating_subcommands = re.compile(
        r"\bgh\s+(?:pr\s+(?:create|merge|close|edit|comment|reopen|review|ready)"
        r"|issue\s+(?:create|close|edit|comment|reopen|delete|transfer|lock)"
        r"|release\s+(?:create|edit|delete|upload)"
        r"|api\s+(?:-X\s+(?:POST|PATCH|PUT|DELETE)|--method\s+(?:POST|PATCH|PUT|DELETE))"
        r")",
        re.IGNORECASE,
    )
    for path in all_workflows():
        text = path.read_text()
        if push_re.search(text):
            offenders.append(f"{path.name}: git push targets upstream")
        if gh_write_re.search(text) and gh_mutating_subcommands.search(text):
            for line in text.splitlines():
                if any(u in line for u in upstreams) and gh_mutating_subcommands.search(line):
                    offenders.append(f"{path.name}: gh mutating call against upstream: {line.strip()[:120]}")
    assert not offenders, (
        "Workflow(s) appear to mutate an upstream repo (hard rule #2 violation): "
        f"{offenders}"
    )


# ─── Wall 6: structural sanity ─────────────────────────────────────────


# ─── Wall 7: validate-author skips merge commits ───────────────────────
#
# GitHub's "Update branch" web button (and auto-update of a behind
# branch) produces a merge commit whose committer is
# `GitHub <noreply@github.com>` — the clicker is the author, not the
# committer. validate-author scans committer email (%ce), so without
# `--no-merges` every PR that sits long enough for main to advance and
# need a branch update fails the committer gate. That's a recurring
# catch-22 unrelated to who authored the substantive code. These tests
# pin that merge commits are skipped AND that foreign *non-merge*
# commits are still caught (the gate isn't over-relaxed).

VALIDATE_AUTHOR = WF_DIR / "validate-author.yml"
_GITHUB_WEB_MERGE_COMMITTER = "noreply@github.com"
_NEW_USEMAME_COMMITTER = "248195428+new-usemame@users.noreply.github.com"


def _git(repo: Path, *args: str, committer_email: str = _NEW_USEMAME_COMMITTER):
    """Run a git command in `repo` with deterministic author/committer."""
    env = dict(os.environ)
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(repo),  # isolate from the dev's global git config
            "GIT_AUTHOR_NAME": "tester",
            "GIT_AUTHOR_EMAIL": "tester@example.com",
            "GIT_COMMITTER_NAME": "tester",
            "GIT_COMMITTER_EMAIL": committer_email,
        }
    )
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _committer_scan(repo: Path, base: str, head: str) -> list[str]:
    """Mirror the workflow's scan command and return the committer
    emails it would inspect for the range base..head."""
    out = _git(repo, "log", "--no-merges", "--format=%H %ce", f"{base}..{head}")
    return [line.split()[1] for line in out.stdout.splitlines() if line.strip()]


def test_validate_author_uses_no_merges_flag():
    """Source-pin: the committer scan must use `--no-merges`. If this
    regresses, web-UI branch updates will block auto-merge again."""
    assert VALIDATE_AUTHOR.exists(), f"missing {VALIDATE_AUTHOR}"
    text = VALIDATE_AUTHOR.read_text()
    scan_line = next(
        (ln for ln in text.splitlines() if "git log" in ln and "%ce" in ln),
        None,
    )
    assert scan_line is not None, "validate-author.yml committer-scan git log line not found"
    assert "--no-merges" in scan_line, (
        "validate-author.yml committer scan must pass --no-merges so the "
        f"GitHub web-UI 'Update branch' merge commit (committer {_GITHUB_WEB_MERGE_COMMITTER}) "
        "doesn't break the gate. Found: " + scan_line.strip()
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_no_merges_skips_web_update_merge_commit_but_keeps_real_commits():
    """Behavioral: a web-UI 'Update branch' merge commit (committer
    noreply@github.com) is excluded from the scan, while the PR's own
    new-usemame commit is still inspected."""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        _git(repo, "init", "-b", "main")
        (repo / "a.txt").write_text("base\n")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-m", "base")
        base = _git(repo, "rev-parse", "HEAD").stdout.strip()

        # PR branch with one legitimate new-usemame commit.
        _git(repo, "checkout", "-b", "feature")
        (repo / "b.txt").write_text("pr work\n")
        _git(repo, "add", "b.txt")
        _git(repo, "commit", "-m", "pr commit")
        pr_commit = _git(repo, "rev-parse", "HEAD").stdout.strip()

        # main advances.
        _git(repo, "checkout", "main")
        (repo / "c.txt").write_text("main moved\n")
        _git(repo, "add", "c.txt")
        _git(repo, "commit", "-m", "main advances")

        # 'Update branch' merge: committer is GitHub's web identity.
        _git(repo, "checkout", "feature")
        _git(
            repo,
            "merge",
            "main",
            "--no-ff",
            "-m",
            "Merge branch 'main' into feature",
            committer_email=_GITHUB_WEB_MERGE_COMMITTER,
        )
        merge_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
        head = merge_sha

        committers = _committer_scan(repo, base, head)
        # The merge commit's foreign committer must NOT appear (skipped).
        assert _GITHUB_WEB_MERGE_COMMITTER not in committers, (
            "web-UI merge commit committer leaked into the scan despite --no-merges"
        )
        # The real PR commit must still be inspected.
        scanned_shas = _git(
            repo, "log", "--no-merges", "--format=%H", f"{base}..{head}"
        ).stdout.split()
        assert pr_commit in scanned_shas, "PR commit dropped from scan"
        assert merge_sha not in scanned_shas, "merge commit not skipped by --no-merges"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
def test_no_merges_still_catches_foreign_non_merge_commit():
    """Guard against over-relaxing: a *non-merge* commit by a foreign
    committer must still be inspected (and would be flagged BAD)."""
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        _git(repo, "init", "-b", "main")
        (repo / "a.txt").write_text("base\n")
        _git(repo, "add", "a.txt")
        _git(repo, "commit", "-m", "base")
        base = _git(repo, "rev-parse", "HEAD").stdout.strip()

        _git(repo, "checkout", "-b", "feature")
        (repo / "b.txt").write_text("sneaky\n")
        _git(repo, "add", "b.txt")
        _git(repo, "commit", "-m", "foreign commit", committer_email="attacker@evil.example")
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()

        committers = _committer_scan(repo, base, head)
        assert "attacker@evil.example" in committers, (
            "--no-merges must not hide foreign non-merge commits from the gate"
        )


# ─── Wall 8: image-building test jobs authenticate to GHCR ─────────────
#
# The Dockerfile COPYs from the private ghcr.io/new-usemame/pbs-cache
# mirror, so any tests.yml job that builds the image from source must
# (a) hold `packages: read` and (b) run a docker/login-action step
# against ghcr.io before the build. Without both, BuildKit's pull of
# the mirror is anonymous and dies with 401 — which continue-on-error
# then masks on main pushes, leaving integration coverage silently
# dead (observed 2026-07-03, run 28677049810).


def _steps(job: dict) -> list[dict]:
    return [s for s in (job.get("steps") or []) if isinstance(s, dict)]


def test_integration_tests_job_authenticates_to_ghcr():
    """The integration-tests job builds the image from source, so it
    needs packages:read + a ghcr.io login step ordered before the
    docker build step. This went missing while the dev/e2e jobs had
    it, and the job failed 401 on every main push unnoticed."""
    wf = _load(WF_DIR / "tests.yml")
    job = (wf.get("jobs") or {}).get("integration-tests")
    assert isinstance(job, dict), "tests.yml must define an integration-tests job"

    perms = job.get("permissions") or {}
    assert perms.get("packages") == "read", (
        "integration-tests must grant `packages: read` so the private "
        "pbs-cache mirror the Dockerfile COPYs from can be pulled"
    )

    steps = _steps(job)
    login_idx = next(
        (
            i
            for i, s in enumerate(steps)
            if str(s.get("uses", "")).startswith("docker/login-action")
            and (s.get("with") or {}).get("registry") == "ghcr.io"
        ),
        None,
    )
    assert login_idx is not None, (
        "integration-tests must log in to ghcr.io before building — the "
        "Dockerfile COPYs from the private pbs-cache mirror and an "
        "anonymous pull 401s"
    )
    build_idx = next(
        (
            i
            for i, s in enumerate(steps)
            if str(s.get("uses", "")).startswith("docker/build-push-action")
        ),
        None,
    )
    assert build_idx is not None, "integration-tests must build the image"
    assert login_idx < build_idx, (
        "GHCR login must come before the image build, or BuildKit still "
        "pulls the pbs-cache mirror anonymously"
    )


# ─── Wall 6: manifest-merge jobs don't boot BuildKit ───────────────────
#
# `docker buildx imagetools create` is a registry-only operation: it
# reads the per-arch manifests and writes a manifest list. It needs no
# builder. But `docker/setup-buildx-action` defaults to the
# docker-container driver, which boots moby/buildkit pulled from Docker
# Hub — so a job that only merges manifests took a hard dependency on a
# third-party registry it never otherwise touches.
#
# Docker Hub egress from GitHub-hosted runners intermittently times out.
# On 2026-07-27 three consecutive dev builds died at "booting buildkit"
# with `Get "https://registry-1.docker.io/v2/": context deadline
# exceeded` — after both arch builds had already succeeded and pushed.
# The :dev tag went stale and the household canary stopped receiving
# merges. The same shape sits in the release workflow's merge job, where
# it is worse: the tag publishes, the manifest never lands, and
# `docker pull ...:vX.Y.Z` 404s for everyone (the v4.0.169 failure mode).
#
# So: a job that merges manifests and does not itself build an image
# must not set up a container-driver buildx.


def _job_text(job: dict) -> str:
    """Flatten every step's `run` + `uses` into one searchable string."""
    parts = []
    for step in _steps(job):
        parts.append(str(step.get("run", "")))
        parts.append(str(step.get("uses", "")))
    return "\n".join(parts)


def test_manifest_merge_jobs_do_not_boot_container_buildkit():
    """Registry-only manifest merges must not pull moby/buildkit.

    Applies to any job that runs `imagetools create` but never builds an
    image. Such a job may either omit setup-buildx-action entirely (the
    buildx CLI plugin is preinstalled on GitHub runners) or pin
    `driver: docker`, which reuses the local dockerd. What it may not do
    is take the default container driver.
    """
    offenders = []
    for path in all_workflows():
        wf = _load(path)
        for job_name, job in (wf.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            text = _job_text(job)
            if "imagetools create" not in text:
                continue
            # A job that genuinely builds an image needs a real builder.
            builds = "docker/build-push-action" in text or re.search(
                r"docker\s+buildx\s+build\b", text
            )
            if builds:
                continue
            for step in _steps(job):
                if not str(step.get("uses", "")).startswith(
                    "docker/setup-buildx-action"
                ):
                    continue
                driver = (step.get("with") or {}).get("driver")
                if driver != "docker":
                    offenders.append(f"{path.name}:{job_name}")
    assert not offenders, (
        "Manifest-merge job(s) boot a container-driver BuildKit for a "
        "registry-only `imagetools create`, taking a needless Docker Hub "
        "dependency that has already broken the dev channel: "
        f"{offenders}. Drop the setup-buildx step or pin `driver: docker`."
    )


def test_all_workflows_have_minimum_permissions_block():
    """Every workflow that does any mutating action (commenting,
    labeling, merging) must have an explicit top-level OR job-level
    permissions block. Implicit GITHUB_TOKEN permissions are too
    broad for CI surfaces this sensitive. Workflows that are pure
    read-only (test runners) can omit the block — we only flag
    workflows whose run blocks reference mutating gh commands.
    """
    offenders = []
    mutating = re.compile(
        r"\bgh\s+(?:pr|issue|release)\s+(?:create|merge|close|edit|comment|reopen|review|ready|delete|upload)\b",
        re.IGNORECASE,
    )
    for path in all_workflows():
        text = path.read_text()
        if not mutating.search(text):
            continue
        wf = _load(path)
        top = wf.get("permissions")
        any_job_perms = any(
            isinstance(job, dict) and "permissions" in job
            for job in (wf.get("jobs") or {}).values()
        )
        if top is None and not any_job_perms:
            offenders.append(path.name)
    assert not offenders, (
        "Workflow(s) with mutating gh calls but no explicit permissions block: "
        f"{offenders}"
    )


# ---------------------------------------------------------------------------
# 6. The SPA e2e job seeds every admin toggle its specs actually exercise.
#
# login-redirect.spec.ts drives a REAL magic-link flow: it POSTs
# /api/v1/auth/magic-link/start from a logged-out page and asserts a 2xx.
# That endpoint is gated on config_remote_login, which is `default=False`
# (cps/config_sql.py). A fresh e2e container therefore answered 403
# magic_link_disabled and the spec failed on the FIXTURE, not the product —
# on every branch, including a CHANGELOG-only one. A permanently-red
# advisory gate teaches everyone to ignore it, which is worse than no gate.
#
# This pins the seed so the fixture can't silently regress again.
# ---------------------------------------------------------------------------


def _e2e_steps() -> list[dict]:
    wf = _load(WF_DIR / "tests.yml")
    job = (wf.get("jobs") or {}).get("e2e-tests") or {}
    return [s for s in (job.get("steps") or []) if isinstance(s, dict)]


def test_e2e_job_enables_remote_login_before_running_the_harness():
    steps = _e2e_steps()
    assert steps, "tests.yml has no e2e-tests steps — did the job get renamed?"

    def _index_of(pred) -> int:
        for i, step in enumerate(steps):
            if pred(step):
                return i
        return -1

    seed_idx = _index_of(
        lambda s: "remote_login" in (s.get("run") or "")
        and "/api/v1/admin/security" in (s.get("run") or "")
    )
    assert seed_idx >= 0, (
        "The e2e job never enables config_remote_login. login-redirect.spec.ts "
        "POSTs /api/v1/auth/magic-link/start logged-out and asserts 2xx, but that "
        "endpoint 403s ('magic_link_disabled') while the admin toggle is off, and "
        "the toggle defaults to False on a fresh container. Seed it via "
        "POST /api/v1/admin/security {\"remote_login\": true} before the harness runs."
    )

    harness_idx = _index_of(lambda s: "npm run test:e2e" in (s.get("run") or ""))
    assert harness_idx >= 0, "tests.yml e2e job no longer runs `npm run test:e2e`"
    assert seed_idx < harness_idx, (
        "remote_login is enabled AFTER the Playwright harness runs — the specs "
        "will still see the feature disabled."
    )


def test_e2e_remote_login_seed_fails_loudly_when_the_endpoint_regresses():
    """The seed must verify the logged-out endpoint, not just flip the toggle.

    Flipping the flag and hoping is how this class of failure hides: the toggle
    write succeeds, something else breaks the endpoint, and the operator gets 15
    minutes of confusing spec failures instead of one clear seed error.
    """
    steps = _e2e_steps()
    seed = next(
        (
            s
            for s in steps
            if "remote_login" in (s.get("run") or "")
            and "/api/v1/admin/security" in (s.get("run") or "")
        ),
        None,
    )
    assert seed is not None, "no remote_login seed step (see the test above)"
    run = seed.get("run") or ""

    assert "/api/v1/auth/magic-link/start" in run, (
        "The seed enables remote_login but never checks that "
        "/api/v1/auth/magic-link/start actually answers for a logged-out client. "
        "Assert it in the seed so a regression fails here, loudly, instead of "
        "resurfacing as an opaque spec failure later."
    )
    assert "::error::" in run, "seed step should emit a GitHub ::error:: annotation on failure"
    assert "exit 1" in run, "seed step must fail the job when the fixture is broken"


def test_changed_paths_treats_tests_yml_as_frontend_relevant():
    """A change to the e2e harness must be able to run the e2e harness.

    The e2e job's container setup and seed steps live in tests.yml. While the
    detector only matched frontend/ and cps/static/app/, a PR fixing the e2e
    fixture could not be validated by the e2e job — the fix for a red gate
    would not run the gate.
    """
    wf = _load(WF_DIR / "tests.yml")
    detect = next(
        (
            s
            for s in ((wf.get("jobs") or {}).get("changed_paths") or {}).get("steps", [])
            if isinstance(s, dict) and s.get("id") == "detect"
        ),
        None,
    )
    assert detect is not None, "changed_paths has no `detect` step"
    run = detect.get("run") or ""
    assert "workflows/tests" in run.replace("\\", ""), (
        "changed_paths does not treat .github/workflows/tests.yml as frontend-relevant, "
        "so a PR that changes the e2e seed/setup will skip the e2e job that it changes."
    )


# ─── Wall 10: integration coverage follows CONTENT, not a label ────────
#
# `integration-tests` is the only job in the tree that builds the Docker
# image and runs the real ingest/Calibre flow. Until now it fired only
# when a PR carried the `safe-tier-2` label (plus main/dev pushes and
# manual dispatch). Nothing keyed on what the PR actually touched.
#
# So the single highest-blast-radius change class in the repo — the
# Dockerfile, which pins CALIBRE_RELEASE / PYTHON_VERSION /
# KEPUBIFY_RELEASE, i.e. the binaries every conversion and ingest runs
# on — sailed through with the image never built, Calibre never
# downloaded, the container never started. And because a *skipped* job's
# result is `skipped` rather than `failure`, Test Suite Summary reported
# SUCCESS. Observed on #1261 (community PR bumping Calibre 9.1.0 →
# 9.11.0): green summary, zero Docker validation behind it.
#
# Three properties restore the gate, and all three are needed — fixing
# any one alone still leaves a hole:
#
#   a. changed_paths classifies build-definition edits (`build` output).
#   b. integration-tests runs, and *gates*, when that output is true.
#   c. Test Suite Summary hard-fails on a red integration job for those
#      PRs. Without (c) the job runs, goes red, and the summary is still
#      green — a gate that reports its own failure as success.
#
# One deliberate limit, recorded rather than glossed: fork PRs RUN the job
# but do not hard-gate on it yet. The build pins the private pbs-cache
# mirror, and whether a fork PR's read-only GITHUB_TOKEN can pull that
# package is not established either way. Blocking community PRs on an
# unverified infrastructure question is worse than surfacing the result
# and reading it before merge. Tracked in #1263.
#
# What this must NOT become: making the mirror or its login conditional to
# accommodate forks. test_dockerfile_contributor_build.py owns that pair of
# invariants — a conditional login against an unconditional mirror is the
# exact shape that silently broke release dry-runs.


def _detect_step() -> dict:
    wf = _load(WF_DIR / "tests.yml")
    step = next(
        (
            s
            for s in ((wf.get("jobs") or {}).get("changed_paths") or {}).get("steps", [])
            if isinstance(s, dict) and s.get("id") == "detect"
        ),
        None,
    )
    assert step is not None, "changed_paths has no `detect` step"
    return step


def _run_detect(repo: Path, base: str, head: str) -> dict[str, str]:
    """Execute the detect step's REAL shell script against a throwaway git
    repo and return the key/values it wrote to $GITHUB_OUTPUT.

    This runs the shipped script rather than re-implementing its regex, so
    the test cannot drift from the workflow the way a source-pin can.
    """
    script = _detect_step().get("run") or ""
    out_file = repo / "_gh_output"
    out_file.write_text("")
    env = dict(os.environ)
    env.update(
        {
            "BASE_SHA": base,
            "HEAD_SHA": head,
            "GITHUB_OUTPUT": str(out_file),
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(repo),
        }
    )
    # `bash -e` mirrors the default shell GitHub Actions uses for `run:`.
    subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    out: dict[str, str] = {}
    for line in out_file.read_text().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key] = value
    return out


def _repo_touching(repo: Path, paths: list[str]) -> tuple[str, str]:
    """Build a git repo whose PR branch changes exactly `paths`."""
    _git(repo, "init", "-b", "main")
    (repo / "seed.txt").write_text("base\n")
    _git(repo, "add", "seed.txt")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()

    _git(repo, "checkout", "-b", "pr")
    for rel in paths:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("changed\n")
        _git(repo, "add", rel)
    _git(repo, "commit", "-m", "pr change")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return base, head


# Each row is (changed files, expected `build`, expected `frontend`).
_DETECT_MATRIX = [
    # The motivating case: the Dockerfile pins the Calibre/Python/kepubify
    # versions the whole runtime is built from.
    (["Dockerfile"], "true", "false"),
    (["requirements.txt"], "true", "false"),
    (["requirements-dev.txt"], "true", "false"),
    # optional-requirements.txt is pip-installed into the image next to
    # requirements.txt. A `^requirements` pattern misses it silently.
    (["optional-requirements.txt"], "true", "false"),
    # root/ is copied to / at build time — the s6 service definitions that
    # decide whether the container boots at all. A break here is a total
    # outage, and only booting the image catches it.
    (["root/etc/s6-overlay/s6-rc.d/cwa-ingest-service/run"], "true", "false"),
    # A PR that edits the integration suite must run the integration suite,
    # for the same reason tests.yml counts as frontend-relevant: otherwise
    # the fix for a broken gate never runs the gate.
    (["tests/docker/test_container_boot.py"], "true", "false"),
    (["tests/integration/test_ingest_flow.py"], "true", "false"),
    # tests.yml houses both harnesses' setup, so it is relevant to both.
    ([".github/workflows/tests.yml"], "true", "true"),
    # Negative cases — these must NOT pay the ~15-minute Docker build.
    (["cps/admin.py"], "false", "false"),
    (["README.md"], "false", "false"),
    # The requirements pattern was widened to catch optional-requirements.txt;
    # pin that the widening stays bounded to the repo root, where the image's
    # pip install reads from. A nested one is not a build input.
    (["docs/requirements.txt"], "false", "false"),
    (["cps/translations/de/LC_MESSAGES/messages.po"], "false", "false"),
    (["frontend/src/App.tsx"], "false", "true"),
    # Mixed PR: one build-relevant path anywhere in the diff is enough.
    (["README.md", "Dockerfile"], "true", "false"),
]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")
@pytest.mark.parametrize("changed,want_build,want_frontend", _DETECT_MATRIX)
def test_changed_paths_classifies_build_definition_edits(changed, want_build, want_frontend):
    """Behavioural: run the shipped detect script over a real diff.

    RED before the fix — the script emitted no `build` key at all, so a
    Dockerfile PR had nothing that could turn the integration gate on.
    """
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        base, head = _repo_touching(repo, changed)
        out = _run_detect(repo, base, head)

    assert "build" in out, (
        "changed_paths emits no `build` output, so nothing can gate the Docker "
        f"integration suite on content. Changed: {changed}"
    )
    assert out["build"] == want_build, (
        f"changed={changed} → build={out['build']!r}, expected {want_build!r}. "
        "A Dockerfile/requirements/integration-suite edit must run the Docker "
        "integration job; anything else must not pay for it."
    )
    assert out["frontend"] == want_frontend, (
        f"changed={changed} → frontend={out['frontend']!r}, expected {want_frontend!r}"
    )


def test_integration_tests_gate_keys_on_changed_paths():
    """The integration job must consume the `build` output — and keep
    working on non-PR events, where changed_paths is skipped."""
    wf = _load(WF_DIR / "tests.yml")
    job = (wf.get("jobs") or {}).get("integration-tests")
    assert isinstance(job, dict), "tests.yml must define an integration-tests job"

    needs = job.get("needs") or []
    needs = [needs] if isinstance(needs, str) else needs
    assert "changed_paths" in needs, (
        "integration-tests must depend on changed_paths to read its `build` output"
    )

    condition = str(job.get("if") or "")
    assert "needs.changed_paths.outputs.build" in condition, (
        "integration-tests still gates only on the safe-tier-2 label. A Dockerfile "
        "PR without that label skips the only job that builds the image."
    )
    # changed_paths is PR-only, so a plain `needs` would drag main/dev pushes
    # and tag runs into `skipped` — silently turning integration coverage off
    # everywhere. Same trap the e2e job documents.
    assert "always()" in condition, (
        "integration-tests needs `always()` in its `if:` — changed_paths is "
        "PR-only, and a skipped dependency would otherwise skip this job on "
        "main/dev pushes too"
    )
    assert "refs/heads/main" in condition, (
        "the main-push path must survive: integration coverage on main is what "
        "auto-revert reads"
    )


def test_integration_tests_actually_blocks_build_prs():
    """Running is not gating. On a build-definition PR the job must be
    mandatory (continue-on-error false), not advisory."""
    wf = _load(WF_DIR / "tests.yml")
    job = (wf.get("jobs") or {}).get("integration-tests")
    coe = str(job.get("continue-on-error", ""))
    assert "needs.changed_paths.outputs.build" in coe, (
        "integration-tests stays advisory (continue-on-error) for any PR without "
        "the safe-tier-2 label — including Dockerfile PRs. A red build would not "
        "block the merge."
    )
    assert "safe-tier-2" in coe, "the existing tier-2 hard gate must be preserved"


def test_summary_hard_fails_on_red_integration_for_build_prs():
    """Layer 3: the summary is what branch protection reads. If it only
    hard-fails for tier-2, a Dockerfile PR whose image fails to build still
    reports a green Test Suite Summary."""
    wf = _load(WF_DIR / "tests.yml")
    jobs = wf.get("jobs") or {}
    summary = next(
        (j for name, j in jobs.items() if isinstance(j, dict) and j.get("name") == "Test Suite Summary"),
        None,
    )
    assert summary is not None, "tests.yml must define a Test Suite Summary job"

    text = _job_text(summary)
    env_blob = "\n".join(str((s.get("env") or {})) for s in _steps(summary))
    combined = text + "\n" + env_blob

    assert "needs.changed_paths.outputs.build" in combined, (
        "Test Suite Summary decides pass/fail using only the safe-tier-2 label. "
        "A build-definition PR with a FAILED integration job is reported as "
        "'advisory' and the summary goes green — the gate reports its own "
        "failure as success."
    )
    needs = summary.get("needs") or []
    needs = [needs] if isinstance(needs, str) else needs
    assert "changed_paths" in needs, "summary must depend on changed_paths to read `build`"


def test_fork_prs_run_the_build_job_but_do_not_hard_gate_on_it_yet():
    """Fork build PRs must RUN integration-tests — that is the whole point —
    but must not hard-gate on it while one question is unresolved.

    The build selects the private pbs-cache mirror, and the login falls back
    to GITHUB_TOKEN when GH_PAT is out of scope (fork PRs get no secrets).
    Whether that read-only token can pull the package is not established
    either way. Hard-gating on an unverified infrastructure question would
    block community PRs for a reason that has nothing to do with their
    change; skipping the job entirely is the bug this module exists to fix.
    So: run it, surface it, read it before merging — and promote to a hard
    gate once a real fork run answers the question (#1263).

    This test is the tripwire on that decision: if someone widens the hard
    gate to forks, they must come here and record why it is now safe.
    """
    wf = _load(WF_DIR / "tests.yml")
    job = (wf.get("jobs") or {}).get("integration-tests")

    condition = str(job.get("if") or "")
    assert "fork" not in condition, (
        "integration-tests must still RUN on fork build PRs — excluding forks "
        "from the `if:` restores the original hole for exactly the "
        "contributions that need the check most"
    )

    coe = str(job.get("continue-on-error", ""))
    assert "fork" in coe, (
        "the hard gate must exclude fork PRs while mirror access from a fork "
        "is unverified (#1263)"
    )


def test_gate_hardness_agrees_between_the_job_and_the_summary():
    """The job's continue-on-error and the summary's IS_BUILD_PR decide the
    same thing from two places. If they drift, one says 'advisory' while the
    other fails the run (or worse, both go soft and nothing gates)."""
    wf = _load(WF_DIR / "tests.yml")
    jobs = wf.get("jobs") or {}
    coe = str((jobs.get("integration-tests") or {}).get("continue-on-error", ""))
    summary = next(
        (j for _n, j in jobs.items() if isinstance(j, dict) and j.get("name") == "Test Suite Summary"),
        None,
    )
    assert summary is not None
    is_build = ""
    for step in _steps(summary):
        env = step.get("env") or {}
        if "IS_BUILD_PR" in env:
            is_build = str(env["IS_BUILD_PR"])
    assert is_build, "summary must define IS_BUILD_PR"

    # Both must key on the build output AND both must carve out forks.
    for name, expr in (("continue-on-error", coe), ("IS_BUILD_PR", is_build)):
        assert "needs.changed_paths.outputs.build" in expr, f"{name} ignores the build output"
        assert "fork" in expr, (
            f"{name} does not carve out fork PRs, but the other one does — the "
            "job and the summary would disagree about what is gated"
        )


def test_ci_image_build_never_selects_the_mirror_without_credentials():
    """The mirror may only be selected where credentials actually exist.

    `test_dockerfile_contributor_build.py` owns the exact-value pinning; the
    approved selector is imported from there so the two cannot drift. Restated
    in this module because it is what a future change to the integration gate
    gets read alongside.

    The bug this guards is one specific shape: **the build selects the private
    mirror while the credentials step is skipped**. That is what broke release
    `workflow_dispatch` dry-runs, and it is why the login below must stay
    unconditional.

    A fork-conditional *source* is the inverse of that shape and is allowed.
    #1262 reverted one on the grounds that its premise was unverified — whether
    a fork's read-only token could pull the mirror was not established either
    way, and the walls were kept rather than weakened on a hunch. #1263 then
    established it empirically: the login succeeds and the manifest HEAD still
    403s, because `packages: read` on a fork token does not reach a private
    package in this owner's scope. So the mirror is deselected exactly where
    the credentials provably cannot work, and the login is never skipped while
    the mirror is selected. Credentials-present contexts are untouched.
    """
    # Load the sibling module by path rather than by name: `tests/unit` is not
    # a package on sys.path, so a bare import resolves only by accident of
    # rootdir. Importing the constant (rather than restating it) is the point —
    # a restated copy is what lets the two modules drift.
    import importlib.util

    _sibling = Path(__file__).resolve().parent / "test_dockerfile_contributor_build.py"
    _spec = importlib.util.spec_from_file_location("_pbs_pins", _sibling)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    FORK_AWARE_PBS_SOURCE = _mod.FORK_AWARE_PBS_SOURCE

    wf = _load(WF_DIR / "tests.yml")
    job = (wf.get("jobs") or {}).get("integration-tests")

    build_step = next(
        (s for s in _steps(job) if str(s.get("uses", "")).startswith("docker/build-push-action")),
        None,
    )
    assert build_step is not None, "integration-tests must build the image"
    selector = [
        line.strip()
        for line in str((build_step.get("with") or {}).get("build-args", "")).splitlines()
        if line.strip().startswith("PBS_SOURCE=")
    ]
    assert selector in (["PBS_SOURCE=ghcr"], [FORK_AWARE_PBS_SOURCE]), (
        f"the CI build must select the mirror explicitly — either the bare "
        f"`PBS_SOURCE=ghcr` pin or the approved fork-aware selector. Found "
        f"{selector}. Unpinned sends it back to the release CDN that 404s the "
        f"Actions egress; an ad-hoc conditional is how the fork/non-fork split "
        f"gets it wrong."
    )

    login = next(
        (s for s in _steps(job) if str(s.get("uses", "")).startswith("docker/login-action")),
        None,
    )
    assert login is not None, "integration-tests must define the GHCR login step"
    assert "if" not in login, (
        "the GHCR login must stay UNCONDITIONAL — a conditional login with a "
        "build that can still select the private mirror is the exact shape "
        "that broke release dry-runs"
    )


# --- Layer 2 promotion: the SPA e2e suite must actually gate (#1130) ---------


def _run_summary_script(
    summary: dict, results: dict, env: dict, event_name: str = "pull_request"
) -> tuple[int, str]:
    """Execute the Test Suite Summary script the way Actions would.

    `${{ needs.<job>.result }}` is replaced with the supplied result (anything
    unspecified defaults to `success`, i.e. the job passed), and the `IS_*`
    predicates are passed as real environment variables — which is how the
    workflow already supplies them. Returns (exit code, combined output).

    Running the script beats grepping it: the gate is a shell decision, and
    only executing it proves which way the decision goes.
    """
    step = next(
        (s for s in _steps(summary) if "needs.e2e-tests.result" in str(s.get("run", ""))),
        None,
    )
    assert step is not None, "summary must have a step that reads needs.e2e-tests.result"
    script = str(step["run"])

    def _sub(match: "re.Match[str]") -> str:
        return results.get(match.group(1), "success")

    script = re.sub(r"\$\{\{\s*needs\.([a-z0-9_-]+)\.result\s*\}\}", _sub, script)
    script = re.sub(r"\$\{\{\s*github\.event_name\s*\}\}", event_name, script)
    # Any remaining ${{ … }} would be an unmodelled input; surface it loudly
    # rather than letting bash interpret it as a brace expansion.
    leftover = re.findall(r"\$\{\{.*?\}\}", script)
    assert not leftover, f"unmodelled workflow expressions in the summary script: {leftover}"

    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_e2e_actually_blocks_frontend_prs():
    """Running is not gating — the same lesson integration-tests learned.

    The SPA e2e matrix is Layer 2 of the verification system. It ran on every
    frontend PR while `continue-on-error` was a blanket
    `github.event_name == 'pull_request'`, so a fully red suite reported a
    green job and `needs.e2e-tests.result` came back `success`. #1130 is the
    write-up: 19 specs failed on every run for weeks and nobody saw them,
    which made the "earn the release" gate decorative rather than protective.

    The suite has since been repaired and is green (9 consecutive runs
    including the v4.1.25 and v4.1.26 tag runs), so it has earned the
    promotion the original comment asked for. Forks stay advisory — see the
    tripwire below.
    """
    wf = _load(WF_DIR / "tests.yml")
    job = (wf.get("jobs") or {}).get("e2e-tests")
    assert isinstance(job, dict), "tests.yml must define an e2e-tests job"

    coe = str(job.get("continue-on-error", ""))
    assert coe.strip() != "${{ github.event_name == 'pull_request' }}", (
        "e2e-tests is advisory for EVERY pull request. A frontend PR that "
        "breaks the SPA reports a green job and merges. That is the exact "
        "hole #1130 exists to close."
    )
    assert "fork" in coe, (
        "the e2e advisory carve-out must be scoped to fork PRs (which have no "
        "run history to justify hard-gating), not to all pull requests"
    )


def test_summary_hard_fails_on_red_e2e_for_frontend_prs():
    """Layer 3: the summary is the check branch protection reads.

    Even with the job promoted, the summary used to swallow the result:

        if [[ "${{ needs.e2e-tests.result }}" == "failure" ]]; then
          echo "⛔ E2E tests failed - do not release!"
          # Don't fail the summary, just warn.
        fi

    `Test Suite Summary` is the single required check, and auto-merge.yml
    fires on it. Warning-and-continuing means a red SPA suite still satisfies
    merge-gate condition (a). Both layers have to agree or the gate reports
    its own failure as a pass.
    """
    wf = _load(WF_DIR / "tests.yml")
    jobs = wf.get("jobs") or {}
    summary = next(
        (j for name, j in jobs.items() if isinstance(j, dict) and j.get("name") == "Test Suite Summary"),
        None,
    )
    assert summary is not None, "tests.yml must define a Test Suite Summary job"

    env_blob = "\n".join(str((s.get("env") or {})) for s in _steps(summary))
    assert "needs.changed_paths.outputs.frontend" in env_blob, (
        "Test Suite Summary never consults `frontend`, so it cannot tell a "
        "frontend PR from a .po-only one and treats every red e2e result as "
        "advisory."
    )

    # Behavioural, not a source pin. An earlier version of this test scraped
    # the script for `exit 1` near the e2e branch and passed against the
    # warn-only original, because the regex ran on to fast-tests' exit. So run
    # the real script instead: substitute the job results GitHub would
    # interpolate and assert the exit code the gate is supposed to produce.
    for scenario, results, env, want_rc, why in [
        (
            "frontend PR from this repo, SPA suite red",
            {"e2e-tests": "failure"},
            {"IS_FRONTEND_PR": "true"},
            1,
            "a broken SPA must block the merge — this is the whole of #1130",
        ),
        (
            "fork frontend PR / main push, SPA suite red",
            {"e2e-tests": "failure"},
            {"IS_FRONTEND_PR": "false"},
            0,
            "forks and main pushes stay advisory; failing them would block "
            "community PRs and auto-revert decisions",
        ),
        (
            "frontend PR, SPA suite green",
            {"e2e-tests": "success"},
            {"IS_FRONTEND_PR": "true"},
            0,
            "a green suite must not block",
        ),
        (
            "translations-only PR, e2e skipped entirely",
            {"e2e-tests": "skipped"},
            {"IS_FRONTEND_PR": "false"},
            0,
            "`skipped` is not `failure` — a .po-only PR must not be gated on a "
            "job that never ran",
        ),
    ]:
        rc, out = _run_summary_script(summary, results, env)
        assert rc == want_rc, (
            f"{scenario}: expected exit {want_rc}, got {rc}. {why}\n--- output ---\n{out}"
        )


def test_summary_fails_when_path_detection_itself_failed():
    """The gates are only as trustworthy as the job they derive from.

    `IS_BUILD_PR` and `IS_FRONTEND_PR` are both computed from
    `needs.changed_paths.outputs.*`. When that job fails its outputs come back
    empty, so every dependent job's `if:` evaluates false, both predicates read
    false, and the summary would report a green required check having gated
    nothing — "we could not tell what changed" silently becoming "nothing
    changed". That is the #1130 failure class one level up, and it disables the
    build gate as well as the SPA one.

    Surfaced by a cross-family review pass on PR #1281.
    """
    wf = _load(WF_DIR / "tests.yml")
    jobs = wf.get("jobs") or {}
    summary = next(
        (j for name, j in jobs.items() if isinstance(j, dict) and j.get("name") == "Test Suite Summary"),
        None,
    )
    assert summary is not None, "tests.yml must define a Test Suite Summary job"

    rc, out = _run_summary_script(
        summary, {"changed_paths": "failure"}, {"IS_FRONTEND_PR": "false", "IS_BUILD_PR": "false"}
    )
    assert rc == 1, (
        "changed_paths failed on a PR and the summary still went green. Both "
        f"path-derived gates were silently disabled.\n--- output ---\n{out}"
    )

    # A push/tag run has no changed_paths job at all (it is `if:`-gated to
    # pull_request), so `skipped` there must stay benign — otherwise every
    # main push and every release tag fails this summary.
    rc, out = _run_summary_script(
        summary,
        {"changed_paths": "skipped"},
        {"IS_FRONTEND_PR": "false", "IS_BUILD_PR": "false"},
        event_name="push",
    )
    assert rc == 0, (
        "a push/tag run must not be failed by changed_paths being skipped — it "
        f"is PR-only by design.\n--- output ---\n{out}"
    )


def test_fork_prs_run_e2e_but_do_not_hard_gate_on_it_yet():
    """Fork frontend PRs must RUN the suite but not block on it yet.

    Same reasoning as the integration-tests tripwire: the job pulls a
    published image and overlays the PR's own SPA bundle, and no fork PR has
    exercised that path yet (#1274, the only recent fork PR, touched no
    frontend files and skipped the job). Hard-gating on an unmeasured
    infrastructure path would block community contributions for a reason
    unrelated to their change. Excluding forks from the `if:` instead would
    restore the original hole for exactly the contributions that need the
    check most.

    If someone widens the hard gate to forks, they must come here and record
    why it is now safe.
    """
    wf = _load(WF_DIR / "tests.yml")
    job = (wf.get("jobs") or {}).get("e2e-tests")

    condition = str(job.get("if") or "")
    assert "fork" not in condition, (
        "e2e-tests must still RUN on fork frontend PRs — surfacing the result "
        "is the point; only the hard gate is deferred"
    )

    coe = str(job.get("continue-on-error", ""))
    assert "fork" in coe, (
        "fork PRs must stay advisory until a real fork run shows the "
        "image-pull + bundle-overlay path works without repo secrets"
    )
