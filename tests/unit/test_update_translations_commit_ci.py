"""Keep generated translation commits inside the normal test pipeline.

The translation refresh is executable build input, not inert bookkeeping:
``pybabel update`` can produce catalogue shapes that only the compile tests
catch, and the same workflow regenerates a README that is also under test.
The release preflight now requires a positive Test Suite Summary verdict, so a
translation commit that suppresses CI would also wedge the release train when
it becomes main's head.
"""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "update-translations.yml"
)


def _translation_commit_step() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")) or {}
    matches = [
        step
        for job in (workflow.get("jobs") or {}).values()
        for step in (job.get("steps") or [])
        if isinstance(step, dict) and step.get("name") == "Commit translation updates"
    ]
    assert len(matches) == 1, (
        "update-translations.yml must have exactly one named translation commit "
        f"step; found {len(matches)}"
    )
    return matches[0]


def test_translation_commit_runs_ci_and_keeps_its_canonical_message():
    run = _translation_commit_step().get("run")
    assert isinstance(run, str), "the translation commit step must contain a run script"

    # Assemble the Actions control markers so this source never casually copies
    # one as a single token. GitHub scans the entire generated commit message,
    # and any of these suppresses every workflow for that main commit.
    #
    # All five documented spellings are checked, not just the one this workflow
    # happened to use. Pinning a single variant would let the next edit reach
    # for a synonym and silently restore the defect while this test stayed
    # green -- the gate would still be named in the suite but would no longer
    # deny anything.
    skip = "skip"
    ci = "ci"
    suppression_markers = (
        f"[{skip} {ci}]",
        f"[{ci} {skip}]",
        f"[no {ci}]",
        f"[{skip} actions]",
        f"[actions {skip}]",
    )
    lowered = run.lower()
    present = [marker for marker in suppression_markers if marker in lowered]
    assert not present, (
        f"the translation commit message suppresses CI via {present}; generated "
        "catalogues and README changes must receive the normal Test Suite verdict"
    )
    assert 'git commit -m "Update translations"' in run, (
        "the workflow must emit the canonical, tested translation commit message"
    )
