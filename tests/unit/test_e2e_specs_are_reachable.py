# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every Playwright spec must be reachable by a project CI actually runs.

Playwright selects specs per PROJECT, through `testMatch` and `testIgnore`. That
is opt-in in both directions, so a spec can sit in `frontend/e2e/`, look like
part of the gate, and be matched by no project at all -- the same failure shape
as an unmarked pytest file (#1105), one layer over.

MEASURED 2026-08-19: `npx playwright test --list` reported 459 tests in 66 files
while 67 `*.spec.ts` files existed. The missing one was `subpath.spec.ts`, which
only has a project when `E2E_SUBPATH_URL` is set, and no workflow sets it. So the
reverse-proxy-prefix suite -- the guard for the documented white-page and
reader-404 class -- had never executed in CI.

This asks Playwright itself what it would run rather than parsing the config,
because the config is TypeScript with conditional project construction and a
parser would answer a different question than the runner does.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend"
E2E = FRONTEND / "e2e"
NODE_MODULES = FRONTEND / "node_modules"

#: Specs deliberately reachable only with extra infrastructure, and the switch
#: that turns each on. An entry here is a statement that the spec does NOT guard
#: ordinary CI -- so it needs a reason, the same way a lane opt-out does.
INFRASTRUCTURE_GATED = {
    "subpath.spec.ts":
        "needs the nginx sub-path rig at E2E_SUBPATH_URL (cwn-nginx-571). No "
        "workflow sets that variable, so this spec runs in no CI job. Tracked "
        "as a finding rather than silently tolerated.",
}


def _spec_files() -> set[str]:
    return {path.name for path in E2E.glob("*.spec.ts")}


def _listed_spec_files() -> set[str]:
    result = subprocess.run(
        ["npx", "playwright", "test", "--list"],
        cwd=FRONTEND, capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, (
        "`npx playwright test --list` failed, so this guard cannot tell which "
        "specs CI runs:\n" + (result.stdout + result.stderr)[-3000:]
    )
    # Lines look like: "  [desktop] › reader.spec.ts:12:1 › does a thing"
    return set(re.findall(r"›\s+(\S+\.spec\.ts):", result.stdout))


def _requires_frontend_toolchain():
    if shutil.which("npx") is None or not NODE_MODULES.is_dir():
        if os.environ.get("CI"):
            pytest.fail(
                "npx or frontend/node_modules is missing on CI: this guard "
                "would stop checking that every Playwright spec is reachable. "
                "The Fast Tests job's 'Install frontend dependencies (npm ci)' "
                "step must run before it."
            )
        pytest.skip("frontend toolchain unavailable locally; run npm ci in frontend/")


def test_there_are_specs_to_check():
    """An empty glob would make the assertions below pass over nothing."""
    specs = _spec_files()
    assert len(specs) > 40, f"only {len(specs)} *.spec.ts found under {E2E}"


def test_every_infrastructure_gated_spec_still_exists():
    """A stale allowlist quietly grants an exemption to nothing."""
    missing = sorted(name for name in INFRASTRUCTURE_GATED if not (E2E / name).is_file())
    assert not missing, (
        "these specs are listed as infrastructure-gated but no longer exist; "
        "drop them from INFRASTRUCTURE_GATED: " + ", ".join(missing)
    )


def test_every_spec_is_matched_by_a_project_ci_runs():
    _requires_frontend_toolchain()
    on_disk = _spec_files()
    listed = _listed_spec_files()
    assert listed, "playwright listed no specs at all; the parse or config has drifted"

    unreachable = sorted(on_disk - listed - set(INFRASTRUCTURE_GATED))
    assert not unreachable, (
        "these Playwright specs are matched by no project the default run "
        "includes, so they execute in no CI job:\n  " + "\n  ".join(unreachable)
        + "\n\nEither give a project a testMatch/testIgnore that reaches them, "
          "or record them in INFRASTRUCTURE_GATED with the switch that enables "
          "them and why ordinary CI cannot."
    )


def test_the_gated_list_is_not_hiding_a_spec_that_actually_runs():
    """The allowlist must describe reality in both directions.

    A spec listed as unreachable that Playwright does list means the exemption
    is stale, and stale exemptions are how a real gap gets re-granted later.
    """
    _requires_frontend_toolchain()
    listed = _listed_spec_files()
    wrongly_gated = sorted(set(INFRASTRUCTURE_GATED) & listed)
    assert not wrongly_gated, (
        "these specs are recorded as infrastructure-gated but the default run "
        "does include them; remove them from INFRASTRUCTURE_GATED: "
        + ", ".join(wrongly_gated)
    )
