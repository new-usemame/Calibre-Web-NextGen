# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pin the two CI mitigations for the xdist worker-IPC hang documented
in notes/xdist-worker-ipc-hang-followup-2026-05-21.md.

The hang ate roughly one in three CI runs across the v4.0.110–v4.0.121
window: pytest-xdist's master would wait forever for a worker that's
between assignments, with no currently-executing test for
pytest-timeout to interrupt; only the step-level 10-minute kill
caught it.

Two complementary mitigations:

1. **Push trigger scoped to `[main, dev]`**, NOT `['**']`. Previously a
   fork branch push fired BOTH the `push` event AND the `pull_request`
   event for the same SHA → two parallel Test Suite runs competing for
   the same xdist resources. Halves the surface for the hang.

2. **`--dist=loadfile`** pins every test in a file to a single xdist
   worker, eliminating the work-stealing race. Fewer worker handoffs
   = fewer races.

Both must stay. If a future refactor drops either, these tests fail.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"


def _yaml() -> str:
    return WORKFLOW.read_text()


def _fast_pytest_arguments() -> list[str]:
    job = _yaml().split("\n  fast-tests:\n", 1)[1].split("\n  reflow-tests:\n", 1)[0]
    lines = re.sub(r"\\\s*\n\s*", " ", job).splitlines()
    commands = [line.strip() for line in lines
                if line.strip().startswith("pytest -m ")]
    assert len(commands) == 1, "locate the actual Fast Tests marker invocation"
    return shlex.split(commands[0])


def test_push_trigger_scoped_to_main_dev_only():
    """The `push:` trigger must NOT use `branches: ['**']` (which is the
    dual-trigger surface). Must be `branches: [main, dev]`."""
    src = _yaml()
    # Find the push: trigger's branches list.
    match = re.search(
        r"push:\s*\n(?:\s*#[^\n]*\n)*\s*branches:\s*\[([^\]]+)\]",
        src,
    )
    assert match, "could not locate push.branches in tests.yml"
    branches = [b.strip().strip("'\"") for b in match.group(1).split(",")]
    assert "**" not in branches, (
        "push.branches must NOT include '**' — that re-introduces the "
        "dual-trigger CI surface for fork branches that doubles the xdist "
        "worker-IPC hang rate. See notes/xdist-worker-ipc-hang-followup-2026-05-21.md"
    )
    assert "main" in branches, "push trigger must still fire on main"


def test_pytest_invocation_pins_dist_loadfile():
    """The Fast Tests pytest invocation must include `--dist=loadfile`
    to eliminate work-stealing races between xdist workers."""
    assert "--dist=loadfile" in _fast_pytest_arguments(), (
        "Fast Tests pytest invocation must include `--dist=loadfile` — "
        "eliminates the xdist work-stealing race that hangs fast-tests "
        "runs at the step-level timeout. See "
        "notes/xdist-worker-ipc-hang-followup-2026-05-21.md"
    )


def test_pytest_invocation_still_has_pytest_timeout():
    """Belt-and-suspenders: `--timeout=120 --timeout-method=thread` must
    stay alongside `--dist=loadfile`. They cover different failure
    modes (per-test hang vs worker-IPC hang)."""
    arguments = _fast_pytest_arguments()
    assert "--timeout=120" in arguments, "must keep --timeout=120 (per-test hang gate)"
    assert "--timeout-method=thread" in arguments, "must keep --timeout-method=thread"
