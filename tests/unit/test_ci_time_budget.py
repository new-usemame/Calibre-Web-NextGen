# SPDX-License-Identifier: GPL-3.0-or-later
"""Behaviour of scripts/ci_time_budget.py, fed from recorded runs.

The two .log fixtures are real job logs trimmed to the killed step: Fast Tests
killed at its 15-minute wall on PR #2772 with every test passing (2026-11-19,
job 135429793677), and the E2E job's Playwright install killed while apt
retried an unreachable mirror (job 135550818169).  The jobs JSON is the
Actions API record of that #2772 attempt.  Everything else is built here from
those shapes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import ci_time_budget as ctb


pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ci_time_budget"
KILL_LOG = (FIXTURES / "fast_tests_wall_kill_2772.log").read_text()
NETWORK_LOG = (FIXTURES / "playwright_install_network_kill.log").read_text()
JOBS = json.loads((FIXTURES / "fast_tests_jobs_run_44486088217.json").read_text())
STEP_START = ctb._ts("2026-11-19T08:16:51Z")
STEP_END = ctb._ts("2026-11-19T08:31:54Z")


def _cut(text: str, marker: str) -> tuple[list[str], list[str]]:
    lines = text.splitlines()
    i = next(n for n, line in enumerate(lines) if marker in line)
    return lines[:i], lines[i:]


def _stamp(ts: float) -> str:
    import datetime as dt
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


# --------------------------------------------------------------------------
# Kill classification
# --------------------------------------------------------------------------

def test_a_kill_while_every_test_was_passing_is_growth():
    kill = ctb.classify_kill(KILL_LOG, STEP_START, STEP_END)
    assert kill["kind"] == "grew"
    assert kill["last_progress_pct"] == 100
    # the PR-comment step's "##[error]" right after the wall is not a failure
    assert kill["failures"] == 0


def test_a_failed_test_before_the_wall_makes_the_kill_failing_not_growth():
    text = KILL_LOG.replace("[gw0] [ 99%] PASSED", "[gw0] [ 99%] FAILED", 1)
    assert ctb.classify_kill(text, STEP_START, STEP_END)["kind"] == "failing"


def test_a_faulthandler_dump_is_stuck_and_names_the_test():
    before, wall = _cut(KILL_LOG, "[ 50%]")
    t = ctb._ts(before[-1][:19] + "Z") + 90
    dump = [
        "Timeout (0:01:30)!",
        "Thread 0x00007f3a1c9fe6c0 (most recent call first):",
        '  File "/usr/lib/python3.13/threading.py", line 359 in wait',
        '  File "/home/runner/work/x/x/tests/unit/helpers.py", line 12 in wait_for',
        '  File "/home/runner/work/x/x/tests/unit/test_kobo_sync.py", line 88 in test_sync_waits_for_lock',
    ]
    text = "\n".join(before + [f"{_stamp(t)} {d}" for d in dump]
                     + [f"{_stamp(STEP_END)} ##[error]The action 'Run smoke and unit tests' has timed out after 15 minutes."])
    kill = ctb.classify_kill(text, STEP_START, STEP_END)
    assert (kill["kind"], kill["cause"]) == ("stuck", "dump")
    assert kill["stuck_test"] == "tests/unit/test_kobo_sync.py::test_sync_waits_for_lock"


def test_minutes_without_a_result_line_before_the_wall_is_stuck():
    before, _ = _cut(KILL_LOG, "[ 50%]")
    started = "tests/unit/test_hangs_forever.py::test_never_returns "
    last = ctb._ts(before[-1][:19] + "Z")
    text = "\n".join(before + [f"{_stamp(last)} {started}",
                               f"{_stamp(STEP_END)} ##[error]The action 'x' has timed out after 15 minutes."])
    kill = ctb.classify_kill(text, STEP_START, STEP_END)
    assert (kill["kind"], kill["cause"]) == ("stuck", "silent")
    assert kill["stuck_test"] == "tests/unit/test_hangs_forever.py::test_never_returns"


def test_a_tail_of_mirror_errors_is_stuck_on_the_network():
    kill = ctb.classify_kill(NETWORK_LOG)
    assert (kill["kind"], kill["cause"]) == ("stuck", "network")


# --------------------------------------------------------------------------
# Samples from the jobs API
# --------------------------------------------------------------------------

def _limit(minutes=15, step="Run smoke and unit tests", pinned=None):
    return ctb.Limit("tests.yml", "fast-tests", "Fast Tests (Smoke + Unit)", step, minutes, 0, pinned)


def test_the_recorded_attempt_counts_as_a_kill_at_its_step_limit_only():
    run = JOBS["run"]
    at_15 = ctb.samples_for_limit(_limit(15), [run], {run["id"]: JOBS["jobs"]})
    at_25 = ctb.samples_for_limit(_limit(25), [run], {run["id"]: JOBS["jobs"]})
    assert [s.killed for s in at_15] == [True]
    assert [s.killed for s in at_25] == [False]
    assert at_15[0].seconds == pytest.approx(903)


def _samples(minutes: list[float], killed: int = 0, kill_kind="grew", limit=15, start=0.0):
    out = []
    for i, m in enumerate(minutes):
        out.append(ctb.Sample(run_id=i, attempt=1, job_id=i, event="push", branch="main", created="",
                              conclusion="success", seconds=m * 60, started=start + i * 3600,
                              completed=start + i * 3600 + m * 60))
    for j in range(killed):
        out.append(ctb.Sample(run_id=1000 + j, attempt=1, job_id=1000 + j, event="pull_request",
                              branch="pr", created="", conclusion="failure", seconds=limit * 60,
                              started=0, completed=limit * 60, killed=True,
                              kill={"kind": kill_kind, "cause": None, "stuck_test": None}))
    return out


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------

def test_p90_past_seventy_percent_of_the_limit_raises_it_to_headroom_over_p95():
    v = ctb.judge(_limit(15), _samples([9.0] * 8 + [11.0, 11.5]))
    assert v.verdict == "raise"
    assert v.recommended == 20          # 1.5 x 11.5 = 17.25, up to the next 5


def test_p90_under_seventy_percent_is_ok():
    assert ctb.judge(_limit(15), _samples([9.0] * 8 + [10.0, 10.4])).verdict == "ok"


def test_one_growth_kill_raises_even_when_healthy_runs_look_fine():
    v = ctb.judge(_limit(15), _samples([9.0] * 10, killed=1))
    assert v.verdict == "raise" and v.recommended == 25    # past the wall it hit: 1.5 x 15


def test_a_kill_far_past_the_median_is_an_outlier_for_the_code_not_the_limit():
    v = ctb.judge(_limit(10, "Install Playwright browsers"), _samples([0.8] * 15, killed=1))
    assert v.verdict == "fix-code"
    assert v.kills[0]["cause"] == "outlier"


def test_a_stuck_kill_asks_for_the_code_fix_and_names_the_test():
    samples = _samples([9.0] * 10, killed=1, kill_kind="stuck")
    samples[-1].kill["stuck_test"] = "tests/unit/test_x.py::test_y"
    v = ctb.judge(_limit(15), samples)
    assert v.verdict == "fix-code" and "tests/unit/test_x.py::test_y" in v.reason
    assert v.recommended is None


def test_shorten_needs_twenty_quiet_runs_and_cuts_to_headroom_over_the_slowest():
    v = ctb.judge(_limit(60, None), _samples([3.0] * 19 + [4.2]))
    assert v.verdict == "shorten" and v.recommended == 10    # 1.5 x 4.2 = 6.3 -> 10
    assert ctb.judge(_limit(60, None), _samples([3.0] * 18 + [4.2])).verdict == "ok"


def test_a_pin_or_a_growing_window_blocks_shortening():
    quiet = _samples([3.0] * 19 + [4.2])
    assert ctb.judge(_limit(60, None, pinned="wait budget"), quiet).verdict == "ok"
    assert ctb.judge(_limit(60, None), quiet, growth={"window_growth": 0.4}).verdict == "ok"


# --------------------------------------------------------------------------
# Naming what grew
# --------------------------------------------------------------------------

def _xdist_log(file_seconds: dict[str, float]) -> str:
    t, lines = 1_000_000.0, []
    for path, secs in file_seconds.items():
        for k in range(3):
            t += secs / 3
            lines.append(f"{_stamp(t)} [gw0] [ 50%] PASSED {path}::test_{k} ")
    return "\n".join(lines)


def test_growth_in_one_file_is_named_and_called_concentrated():
    base = {f"tests/unit/test_{n}.py": 6.0 for n in range(10)}
    slow = dict(base, **{"tests/unit/test_3.py": 186.0})
    g = ctb.growth_by_file(ctb.per_file_seconds(_xdist_log(base)), ctb.per_file_seconds(_xdist_log(slow)))
    assert g["concentrated"] and g["top"][0]["file"] == "tests/unit/test_3.py"
    assert g["top"][0]["delta_s"] == pytest.approx(180, abs=1)


def test_growth_spread_across_many_new_files_is_not_concentrated():
    base = {f"tests/unit/test_{n}.py": 6.0 for n in range(10)}
    more = dict(base, **{f"tests/unit/test_new_{n}.py": 15.0 for n in range(20)})
    g = ctb.growth_by_file(ctb.per_file_seconds(_xdist_log(base)), ctb.per_file_seconds(_xdist_log(more)))
    assert not g["concentrated"] and g["new_files"] == 20


# --------------------------------------------------------------------------
# Workflow parsing and --apply
# --------------------------------------------------------------------------

WORKFLOW = """\
name: T
on: push
jobs:
  fast:
    name: Fast Tests
    # the step's timeout-minutes cannot reach setup
    timeout-minutes: 25
    steps:
      - uses: actions/checkout@v7
        timeout-minutes: 3
      - name: Run tests
        # Long human history of this limit.
        timeout-minutes: 15
        run: |
          pytest
  build:
    name: Build (${{ matrix.arch }})
    # ci-time-budget: keep waits for another workflow
    timeout-minutes: 100
    steps:
    - name: Make
      run: make
"""


def test_every_job_and_step_limit_is_found_with_its_owner():
    found = [(l.job_id, l.step_name, l.minutes, l.pinned) for l in ctb.parse_workflow(WORKFLOW, "t.yml")]
    assert found == [
        ("fast", None, 25, None),
        ("fast", "Run actions/checkout@v7", 3, None),
        ("fast", "Run tests", 15, None),
        ("build", None, 100, "waits for another workflow"),
    ]
    assert ctb.job_name_matches("Build (${{ matrix.arch }})", "Build (arm64)")
    assert not ctb.job_name_matches("Fast Tests", "Fast Tests Summary")


def test_apply_changes_only_the_value_and_one_marker_line():
    limits = ctb.parse_workflow(WORKFLOW, "t.yml")
    step = next(l for l in limits if l.step_name == "Run tests")
    once = ctb.apply_changes(WORKFLOW, [(step, 25, "2026-11-19: raise 15 -> 25 min")])
    added = [l for l in once.splitlines() if l not in WORKFLOW.splitlines()]
    assert added == ["        # ci-time-budget 2026-11-19: raise 15 -> 25 min", "        timeout-minutes: 25"]
    assert once.replace("        # ci-time-budget 2026-11-19: raise 15 -> 25 min\n", "").replace(
        "timeout-minutes: 25\n        run", "timeout-minutes: 15\n        run") == WORKFLOW
    step2 = next(l for l in ctb.parse_workflow(once, "t.yml") if l.step_name == "Run tests")
    twice = ctb.apply_changes(once, [(step2, 30, "2026-12-01: raise 25 -> 30 min")])
    assert twice.count("# ci-time-budget 20") == 1 and "timeout-minutes: 30" in twice


def test_a_raised_step_lifts_its_job_limit_above_step_plus_setup():
    limits = [l for l in ctb.parse_workflow(WORKFLOW, "t.yml") if l.job_id == "fast"]
    job, step = limits[0], limits[2]
    job_samples = []
    for s in _samples([17.0] * 10):          # 17-min jobs whose test step took 13
        s.step_seconds = {"Run tests": 13 * 60}
        job_samples.append(s)
    verdicts = {
        job.key: ctb.judge(job, job_samples),
        limits[1].key: ctb.judge(limits[1], []),
        step.key: ctb.judge(step, _samples([13.0] * 10)),
    }
    assert verdicts[step.key].recommended == 20
    ctb.reconcile_job_limits(limits, verdicts, {job.key: job_samples, limits[1].key: [], step.key: []})
    assert verdicts[job.key].verdict == "raise"
    assert verdicts[job.key].recommended == 30       # 20 + 1.5 x 4-min setup = 26 -> 30
