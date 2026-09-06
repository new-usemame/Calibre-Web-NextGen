# SPDX-License-Identifier: GPL-3.0-or-later
"""Executable regression coverage for the tree-drift server-state E2E guard."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml


pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "tests.yml"


def _guard_body() -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["e2e-tests"]["steps"]
    step = next(step for step in steps if step.get("name") == "Run server-state e2e lane")
    return step["run"]


def _run_guard(
    tmp_path: Path,
    *,
    project_defined: bool,
    spec_present: bool,
    playwright_exit: int = 0,
) -> tuple[subprocess.CompletedProcess[str], str]:
    frontend = tmp_path / "frontend"
    e2e = frontend / "e2e"
    bin_dir = tmp_path / "bin"
    e2e.mkdir(parents=True)
    bin_dir.mkdir()

    project = "{ name: 'server-state-chromium', testMatch: SERVER_STATE_SPECS }," if project_defined else ""
    (frontend / "playwright.config.ts").write_text(
        "const SERVER_STATE_SPECS = [/my-library-admin-intro\\.spec\\.ts/];\n"
        f"export default {{ projects: [{project}] }};\n",
        encoding="utf-8",
    )
    if spec_present:
        (e2e / "my-library-admin-intro.spec.ts").write_text("// synthetic spec\n", encoding="utf-8")

    invocation_log = tmp_path / "playwright-invocation.log"
    npx = bin_dir / "npx"
    npx.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" > \"$PLAYWRIGHT_INVOCATION_LOG\"\n"
        "exit \"$PLAYWRIGHT_EXIT\"\n",
        encoding="utf-8",
    )
    npx.chmod(0o755)

    result = subprocess.run(
        ["bash", "-c", _guard_body()],
        cwd=frontend,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "PLAYWRIGHT_EXIT": str(playwright_exit),
            "PLAYWRIGHT_INVOCATION_LOG": str(invocation_log),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    invocation = invocation_log.read_text(encoding="utf-8").strip() if invocation_log.exists() else ""
    return result, invocation


def test_defined_project_runs_the_hard_gated_lane(tmp_path):
    result, invocation = _run_guard(tmp_path, project_defined=True, spec_present=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert invocation == "playwright test --project=server-state-chromium"

    failed, _ = _run_guard(
        tmp_path / "failure", project_defined=True, spec_present=True, playwright_exit=23
    )
    assert failed.returncode == 23, "the guard must propagate a real lane failure"


def test_absent_project_and_absent_specs_is_an_explicit_skip(tmp_path):
    result, invocation = _run_guard(tmp_path, project_defined=False, spec_present=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert invocation == ""
    assert "::notice::" in result.stdout
    assert "predates the server-state E2E lane" in result.stdout


def test_absent_project_with_owned_specs_is_a_configuration_error(tmp_path):
    result, invocation = _run_guard(tmp_path, project_defined=False, spec_present=True)

    assert result.returncode != 0
    assert invocation == ""
    assert "::error::" in result.stdout
    assert "server-state-chromium" in result.stdout
    assert "my-library-admin-intro.spec.ts" in result.stdout


def test_guard_recognises_the_real_repository_playwright_config(tmp_path):
    """The skip path must never fire on a tree that DOES define the lane.

    Every other case here runs against a synthetic config, so a guard whose
    detection stopped matching the repository's real `playwright.config.ts`
    would silently downgrade the lane to a no-op with the suite still green.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    invocation_log = tmp_path / "playwright-invocation.log"
    npx = bin_dir / "npx"
    npx.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" > \"$PLAYWRIGHT_INVOCATION_LOG\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    npx.chmod(0o755)

    result = subprocess.run(
        ["bash", "-c", _guard_body()],
        cwd=REPO / "frontend",
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "PLAYWRIGHT_INVOCATION_LOG": str(invocation_log),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "::notice::" not in result.stdout, "the real tree defines the lane; it must not be skipped"
    assert invocation_log.exists(), "the guard did not invoke the lane against the real config"
    assert invocation_log.read_text(encoding="utf-8").strip() == (
        "playwright test --project=server-state-chromium"
    )
