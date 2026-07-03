# SPDX-License-Identifier: GPL-3.0-or-later
"""Pin the push-race hardening in update-translations.yml.

The workflow commits regenerated translation files back to main. Anything
else landing on main between its checkout and its push (autopilot docs
backfills, a second run triggered by a quick follow-up merge) used to make
the bare ``git push`` fail non-fast-forward — 3 of the last 30 runs died
this way (runs 28635515788, 28590300379, 28422116634, all with the
identical ``! [rejected] main -> main (fetch first)`` signature).

Two walls, each pinned below:

  1. A ``concurrency`` group serializes runs of this workflow so two
     quick merges can't race each other's translation commit. It must
     NOT cancel in-progress runs — cancelling mid-push could strand a
     half-finished commit.

  2. The commit step retries its push behind ``git pull --rebase`` so a
     race with an external push (docs backfill) recovers instead of
     failing the job.

If either goes red, restore the property in the workflow — don't weaken
the test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover - yaml ships with most distros
    yaml = None  # type: ignore[assignment]

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
WF_PATH = REPO_ROOT / ".github" / "workflows" / "update-translations.yml"


def _load() -> dict:
    if yaml is None:
        pytest.skip("PyYAML not installed in this environment")
    with WF_PATH.open() as fh:
        return yaml.safe_load(fh) or {}


def _commit_step() -> dict:
    wf = _load()
    for step in wf["jobs"]["update-translations"]["steps"]:
        if isinstance(step, dict) and step.get("name") == "Commit translation updates":
            return step
    raise AssertionError(
        "update-translations.yml no longer has a 'Commit translation updates' "
        "step — if it was renamed, update this test's lookup"
    )


def test_workflow_serializes_its_own_runs():
    wf = _load()
    concurrency = wf.get("concurrency")
    assert isinstance(concurrency, dict), (
        "update-translations.yml has no concurrency group: two merges to "
        "main in quick succession run concurrently and the loser's "
        "translation push fails non-fast-forward"
    )
    assert concurrency.get("group"), "concurrency block must set a group"
    assert concurrency.get("cancel-in-progress") is False, (
        "cancel-in-progress must be false: cancelling a run mid-push can "
        "strand a half-finished translation commit"
    )


def test_translation_push_retries_with_rebase():
    script = _commit_step().get("run", "")
    assert "git pull --rebase origin main" in script, (
        "translation push must rebase onto updated main and retry — a bare "
        "`git push` loses the race with autopilot docs pushes to main "
        "(non-fast-forward, 3 failures in 30 runs)"
    )
    assert "if git push" in script, (
        "push must be attempted inside a retry guard, not as a bare "
        "fail-the-job command"
    )
    assert "git rebase --abort" in script, (
        "a rebase conflict must abort cleanly (and fail loudly) rather "
        "than leave the checkout mid-rebase"
    )


def test_push_failure_still_fails_the_job():
    """The retry loop must not swallow a persistent failure: after the
    attempts are exhausted the job has to exit non-zero, otherwise a
    broken push goes green and translations silently stop landing."""
    script = _commit_step().get("run", "")
    assert 'if [ "${pushed}" != "1" ]' in script and "exit 1" in script, (
        "exhausted retries must exit 1 so the run stays red instead of "
        "silently dropping the translation commit"
    )
