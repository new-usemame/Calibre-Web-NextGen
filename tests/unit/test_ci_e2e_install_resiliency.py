# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Keep the mitigations for the observed SPA E2E install timeout.

OBSERVED on PR #2157, run 33848680049 at head 73b5fdc3c3: both the original
attempt and rerun restored the npm and 364 MB Playwright caches, entered
``npm ci`` at 07:45:08, printed only two deprecation warnings by 07:45:10,
and were killed at 07:48:21 by the ``Install Playwright`` step's three-minute
cap. The orphaned process was ``npm ci``; Playwright installation never began.
With the same branch and lockfile, run 33833541562 completed the combined step
from 04:41:44 to 04:42:40 (56 seconds). The three-minute cap therefore sat too
close to a high-variance networked install despite warm caches.

The lasting invariants are behavioral rather than step-name pins: every npm CI
install prefers its restored cache and disables avoidable audit/funding calls;
each separately diagnosable dependency/browser install in the E2E job has an
explicit budget well beyond the observed stall but below the job guard; and the
conditional SPA overlay reuses the unconditional dependency tree installed
earlier in the same working directory.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"

OBSERVED_STALL_SECONDS = 3 * 60 + 13
MINIMUM_INSTALL_BUDGET_SECONDS = OBSERVED_STALL_SECONDS * 3


def _yaml() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _e2e_job(src: str) -> str:
    """Return the job whose displayed name is ``E2E Tests (SPA)``."""
    match = re.search(
        r"(?ms)^  ([A-Za-z0-9_-]+):\n"
        r"(?:(?!^  [A-Za-z0-9_-]+:).)*?"
        r"^    name:\s*E2E Tests \(SPA\)\s*$"
        r"(?P<rest>(?:(?!^  [A-Za-z0-9_-]+:).)*)",
        src,
    )
    assert match, "could not locate the E2E Tests (SPA) job"
    return match.group(0)


def _step_blocks(job: str) -> list[str]:
    """Split one job into step mappings without requiring a YAML package."""
    starts = [m.start() for m in re.finditer(r"(?m)^      - name:\s*", job)]
    assert starts, "E2E Tests (SPA) has no named steps"
    return [
        job[start : starts[index + 1] if index + 1 < len(starts) else len(job)]
        for index, start in enumerate(starts)
    ]


def _run_scripts(src: str) -> list[str]:
    """Extract scalar and block ``run`` scripts from a workflow/job/step."""
    lines = src.splitlines()
    scripts: list[str] = []
    index = 0
    while index < len(lines):
        match = re.match(r"^(?P<indent>\s*)run:\s*(?P<value>.*)$", lines[index])
        if not match:
            index += 1
            continue
        value = match.group("value").strip()
        if value not in {"|", ">", "|-", ">-"}:
            scripts.append(value)
            index += 1
            continue
        indent = len(match.group("indent"))
        index += 1
        body: list[str] = []
        while index < len(lines):
            line = lines[index]
            if line.strip() and len(line) - len(line.lstrip()) <= indent:
                break
            body.append(line.strip())
            index += 1
        scripts.append("\n".join(body))
    return scripts


def _npm_ci_commands(src: str) -> list[list[str]]:
    commands: list[list[str]] = []
    for script in _run_scripts(src):
        for line in script.splitlines():
            for segment in re.split(r"(?:&&|\|\||;)", line):
                segment = segment.strip()
                if re.match(r"^npm\s+ci(?:\s|$)", segment):
                    commands.append(shlex.split(segment))
    return commands


def _step_run(step: str) -> str:
    scripts = _run_scripts(step)
    return "\n".join(scripts)


def _step_timeout_minutes(step: str) -> int | None:
    match = re.search(r"(?m)^        timeout-minutes:\s*(\d+)\s*(?:#.*)?$", step)
    return int(match.group(1)) if match else None


def _job_timeout_minutes(job: str) -> int:
    match = re.search(r"(?m)^    timeout-minutes:\s*(\d+)\s*(?:#.*)?$", job)
    assert match, "E2E Tests (SPA) must retain a job-level hang guard"
    return int(match.group(1))


def _working_directory(step: str) -> str | None:
    match = re.search(r"(?m)^        working-directory:\s*([^#\n]+)", step)
    return match.group(1).strip() if match else None


def test_e2e_install_steps_have_budget_far_beyond_observed_stall():
    """Explicit setup caps must outlive the measured stall yet fail fast."""
    job = _e2e_job(_yaml())
    job_timeout = _job_timeout_minutes(job)
    dependency_steps = [
        step
        for step in _step_blocks(job)
        if re.search(r"\bnpm\s+ci\b", _step_run(step))
    ]
    browser_steps = [
        step
        for step in _step_blocks(job)
        if re.search(r"\bplaywright\s+install\b", _step_run(step))
    ]
    assert len(dependency_steps) == 1, (
        "E2E Tests (SPA) needs one dependency-install step"
    )
    assert len(browser_steps) == 1, (
        "E2E browser setup must be separate so the next stall is diagnosable"
    )

    for step in (*dependency_steps, *browser_steps):
        step_timeout = _step_timeout_minutes(step)
        assert step_timeout is not None, (
            "each E2E dependency/browser install needs its own timeout; the "
            f"{job_timeout}-minute job guard is not a fail-fast step bound:\n{step}"
        )
        step_seconds = step_timeout * 60
        assert step_seconds >= MINIMUM_INSTALL_BUDGET_SECONDS, (
            "E2E dependency/browser installs need at least three times the "
            f"observed {OBSERVED_STALL_SECONDS}s stall, but this step gets "
            f"only {step_seconds}s:\n{step}"
        )
        assert step_timeout < job_timeout, (
            "an install timeout must fail fast before the job-level guard: "
            f"step={step_timeout}m, job={job_timeout}m"
        )


def test_every_npm_ci_is_cache_first_non_auditing_and_quiet():
    """All CI installs avoid optional network calls and routine log noise."""
    commands = _npm_ci_commands(_yaml())
    assert commands, "tests.yml must contain at least one npm ci install"

    for command in commands:
        args = set(command[2:])
        joined = " ".join(command)
        assert "--prefer-offline" in args, f"npm ci must prefer its cache: {joined}"
        assert {"--no-audit", "--audit=false"} & args, (
            f"npm ci must disable the registry audit call: {joined}"
        )
        assert {"--no-fund", "--fund=false"} & args, (
            f"npm ci must disable the funding pass: {joined}"
        )
        quiet = (
            {"--silent", "--quiet", "--loglevel=error"} & args
            or "--loglevel" in args and "error" in args
        )
        assert quiet, f"npm ci must suppress routine CI noise: {joined}"


def test_overlay_reuses_unconditional_dependency_tree_from_earlier_step():
    """The conditional overlay must not reinstall the identical lockfile."""
    job = _e2e_job(_yaml())
    steps = _step_blocks(job)
    overlay_index, overlay = next(
        (index, step)
        for index, step in enumerate(steps)
        if "docker cp" in _step_run(step) and "npm run build" in _step_run(step)
    )
    assert not _npm_ci_commands(overlay), (
        "the SPA overlay must reuse node_modules instead of running npm ci again"
    )

    prior_installs = [
        step for step in steps[:overlay_index] if _npm_ci_commands(step)
    ]
    assert len(prior_installs) == 1, (
        "the overlay needs exactly one earlier dependency install in this job"
    )
    install = prior_installs[0]
    assert _working_directory(install) == _working_directory(overlay) == "frontend", (
        "the earlier npm ci and overlay build must share frontend/ so node_modules "
        "is the same installed tree"
    )
    assert not re.search(r"(?m)^        if:\s*", install), (
        "the earlier npm ci must be unconditional so every reachable overlay has "
        "the installed dependency tree"
    )
    assert not re.search(r"(?m)^        continue-on-error:\s*true\s*$", install), (
        "a failed earlier npm ci must stop the job before the overlay uses its tree"
    )
    overlay_if = re.search(
        r"(?ms)^        if:\s*(?:[>|]-?\s*)?(.*?)(?=^        [A-Za-z_-]+:)",
        overlay,
    )
    assert overlay_if and not re.search(r"\b(?:always|failure)\s*\(", overlay_if.group(1)), (
        "the overlay must retain GitHub's default success gating so it cannot "
        "run after the earlier npm ci fails"
    )
