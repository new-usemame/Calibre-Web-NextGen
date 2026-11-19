# SPDX-License-Identifier: GPL-3.0-or-later
"""Judge every CI time limit against measured run times, and adjust it.

Every ``timeout-minutes`` in ``.github/workflows/*.yml`` is a guess about how
long a job or step takes.  Guesses go stale silently: Fast Tests' 15-minute
step limit shrank from 8.5 minutes of headroom to none between 2026-09-24 and
2026-11-19, and nothing noticed until it killed PRs whose tests had all
passed.  This tool measures instead of guessing:

* finds each job and step limit in the workflows;
* pulls the durations of recent completed runs from the Actions jobs API
  (main first; PR runs count for kills);
* classifies each kill from its log: ``grew`` (tests were still advancing when
  the wall arrived) or ``stuck`` (a faulthandler/pytest-timeout dump, a crashed
  worker, or minutes with no progress);
* when the step has grown, names which test files grew;
* gives one verdict per limit: ``ok``, ``raise``, ``shorten`` or ``fix-code``;
* with ``--apply``, rewrites the recommended ``timeout-minutes`` values and a
  one-line ``# ci-time-budget`` comment above each, and nothing else.

Usage::

    python scripts/ci_time_budget.py --repo new-usemame/Calibre-Web-NextGen
    python scripts/ci_time_budget.py --repo OWNER/REPO --apply
    python scripts/ci_time_budget.py --repo OWNER/REPO --json

Read-only unless ``--apply`` is given; even then it only edits local files.
Stdlib only; talks to GitHub through the ``gh`` CLI (``--gh`` picks another
binary, e.g. a wrapper that selects an identity).
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import datetime as dt
import json
import math
import re
import statistics
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable


# ---------------------------------------------------------------------------
# Thresholds: the one place every judgement number lives.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Thresholds:
    # raise when the 90th percentile of healthy runs uses more than this
    # share of the limit.  At 70% a normal slow runner (GitHub's hosted
    # runners vary 20-30% run to run) still fits; past it, a healthy PR on a
    # slow runner starts dying at the wall, which is what happened to #2772.
    raise_p90_fraction: float = 0.70
    # shorten when the 95th percentile uses less than this share.  A limit
    # three times the worst healthy run only makes a hung run wait longer
    # before it fails; 35% leaves the shortened limit (1.5x p95) well clear.
    shorten_p95_fraction: float = 0.35
    # shortening needs a real sample; one quiet week must not tighten a gate.
    shorten_min_runs: int = 20
    # p90/p95 from fewer runs than this are noise; the limit is reported but
    # not judged on them (a kill is still judged: one kill is evidence).
    judge_min_runs: int = 5
    # the window counts as grown when the median of its newer half exceeds
    # the median of its older half by more than this share.
    growth_fraction: float = 0.25
    growth_min_runs: int = 8
    # growth is "concentrated" (fix the tests, not the limit) when the top
    # few files carry at least this share of the added time.
    concentrated_top_files: int = 3
    concentrated_share: float = 0.60
    # and only when the added time is big enough to be worth chasing.
    concentrated_min_seconds: float = 60.0
    # new limit = headroom x p95, rounded up to the step below.
    headroom: float = 1.5
    round_to_minutes: int = 5
    # one shorten never cuts below this share of the current limit, so a
    # window of cache-hit builds converges in steps (60 -> 30 -> 15) instead
    # of jumping straight under a cold build it never saw.
    shorten_floor_fraction: float = 0.5
    # an advancing kill (grew or outlier) within this many days of this
    # tool's own shorten restores the pre-shorten value: the cut was wrong,
    # and the outlier rule must not blame the code for it.
    shorten_regret_days: int = 30
    # a run counts as killed by its limit when it ended failed/cancelled
    # within this many seconds of the wall (API timestamps are whole seconds).
    kill_slack_seconds: float = 15.0
    # no progress line for this long before the kill means stuck, not slow.
    # pytest-timeout fires at 120s and faulthandler at 90s in Fast Tests, so a
    # healthy run never goes three minutes without a result line.
    stall_seconds: float = 180.0
    # a kill whose last ``stall_seconds`` of output are mostly network errors
    # (apt/npm retrying an unreachable mirror) is stuck on the network: a
    # longer limit only waits longer for the same outage.
    network_min_lines: int = 3
    # an advancing kill at more than this multiple of the healthy median is
    # an outlier, not growth: work does not grow 3x between runs, a crawling
    # mirror or a sick runner does (Install Playwright browsers, 2026-11-19:
    # p50 0.8 min, killed at 10 while apt fetched 30 kB packages in 30 s).
    outlier_factor: float = 3.0
    network_share: float = 0.5
    # shorten to headroom x the slowest healthy run seen (not p95): build
    # times are bimodal (cache hit vs cold build), and the cut must still fit
    # the cold path the window happened to contain.


THRESHOLDS = Thresholds()


# ---------------------------------------------------------------------------
# Workflow parsing
# ---------------------------------------------------------------------------

_TIMEOUT_RE = re.compile(r"^(?P<indent>\s*)(?P<dash>-\s+)?timeout-minutes:\s*(?P<value>\d+)\b")
_KEY_RE = re.compile(r"^(?P<indent>\s*)(?P<dash>-\s+)?(?P<key>[A-Za-z0-9_-]+):\s*(?P<rest>.*)$")
MARKER = "# ci-time-budget"
_SHORTEN_MARK_RE = re.compile(r"#\s*ci-time-budget (\d{4}-\d\d-\d\d): shorten (\d+) -> (\d+) min")
_PIN_RE = re.compile(r"#\s*ci-time-budget:\s*keep\b\s*(.*)$")


def _pin_for(lines: list[str], line_no: int) -> str | None:
    """A ``# ci-time-budget: keep <why>`` inline or in the comment block above.

    Pins a deliberate budget the samples cannot show (a wait for another
    workflow, a rare cold build) against shortening.  Raising is still allowed:
    a kill is evidence even for a pinned limit.
    """
    m = _PIN_RE.search(lines[line_no])
    if m:
        return m.group(1).strip() or "pinned"
    i = line_no - 1
    while i >= 0 and lines[i].strip().startswith("#"):
        m = _PIN_RE.search(lines[i])
        if m:
            return m.group(1).strip() or "pinned"
        i -= 1
    return None


@dataclass
class Limit:
    workflow: str            # file name, e.g. tests.yml
    job_id: str
    job_name: str            # the job's ``name:`` (may hold ${{ }} expressions)
    step_name: str | None    # None for a job-level limit
    minutes: int
    line: int                # 0-based line index of the timeout-minutes line
    pinned: str | None = None  # reason from a "# ci-time-budget: keep ..." comment
    shortened_on: str | None = None   # date of this tool's last shorten, from its marker
    shortened_from: int | None = None  # the value before that shorten

    @property
    def key(self) -> str:
        where = f"{self.job_id}" + (f" / {self.step_name}" if self.step_name else "")
        return f"{self.workflow}: {where}"

    @property
    def scope(self) -> str:
        return "step" if self.step_name else "job"


def _unquote(value: str) -> str:
    value = value.split(" #", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def parse_workflow(text: str, workflow: str) -> list[Limit]:
    """Every job- and step-level ``timeout-minutes`` with its job and step.

    Line-oriented on purpose: ``--apply`` must rewrite one line and leave the
    rest of the file byte-identical, which a YAML round-trip cannot promise.
    Assumes the GitHub Actions layout (``jobs:`` at column 0, job keys one
    level in, ``steps:`` items as ``- `` entries).
    """
    lines = text.splitlines()
    limits: list[Limit] = []
    in_jobs = False
    job_indent: int | None = None
    job_id = job_name = None
    job_limit_lines: list[tuple[int, int]] = []
    steps_indent: int | None = None
    step_item_indent: int | None = None
    step_name: str | None = None
    step_seen_name = False
    pending_step_limits: list[tuple[int, int]] = []

    def flush_step():
        nonlocal pending_step_limits
        for line_no, minutes in pending_step_limits:
            limits.append(Limit(workflow, job_id, job_name or job_id, step_name or "(unnamed step)", minutes, line_no))
        pending_step_limits = []

    def flush_job():
        nonlocal job_limit_lines
        flush_step()
        for line_no, minutes in job_limit_lines:
            limits.append(Limit(workflow, job_id, job_name or job_id, None, minutes, line_no))
        job_limit_lines = []

    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0:
            if job_id is not None:
                flush_job()
                job_id = None
            in_jobs = stripped.startswith("jobs:")
            job_indent = None
            continue
        if not in_jobs:
            continue
        m = _KEY_RE.match(raw)
        if job_indent is None:
            job_indent = indent
        if indent == job_indent and m and not m.group("dash"):
            if job_id is not None:
                flush_job()
            job_id, job_name = m.group("key"), None
            steps_indent = step_item_indent = None
            step_name, step_seen_name = None, False
            continue
        if job_id is None:
            continue
        # leaving the steps list?
        is_item = bool(m and m.group("dash")) and (step_item_indent is None or indent == step_item_indent)
        if steps_indent is not None and indent <= steps_indent and not is_item:
            flush_step()
            steps_indent = step_item_indent = None
            step_name = None
        if m and indent == job_indent + 2 and not m.group("dash") and steps_indent is None:
            if m.group("key") == "name":
                job_name = _unquote(m.group("rest"))
            elif m.group("key") == "steps":
                steps_indent = indent
            elif m.group("key") == "timeout-minutes":
                tm = _TIMEOUT_RE.match(raw)
                if tm:
                    job_limit_lines.append((i, int(tm.group("value"))))
            continue
        if steps_indent is not None:
            if m and m.group("dash"):
                if step_item_indent is None:
                    step_item_indent = indent
                if indent == step_item_indent:
                    flush_step()
                    step_name, step_seen_name = None, False
                    if m.group("key") == "name":
                        step_name, step_seen_name = _unquote(m.group("rest")), True
                    elif m.group("key") == "uses" and not step_seen_name:
                        step_name = "Run " + _unquote(m.group("rest"))
                    tm = _TIMEOUT_RE.match(raw)
                    if tm:
                        pending_step_limits.append((i, int(tm.group("value"))))
                    continue
            if step_item_indent is not None and m and not m.group("dash") and indent == step_item_indent + 2:
                key = m.group("key")
                if key == "name":
                    step_name, step_seen_name = _unquote(m.group("rest")), True
                elif key == "uses" and not step_seen_name:
                    step_name = "Run " + _unquote(m.group("rest"))
                elif key == "timeout-minutes":
                    tm = _TIMEOUT_RE.match(raw)
                    if tm:
                        pending_step_limits.append((i, int(tm.group("value"))))
    if job_id is not None:
        flush_job()
    for limit in limits:
        limit.pinned = _pin_for(lines, limit.line)
        mark = _SHORTEN_MARK_RE.search(lines[limit.line - 1]) if limit.line else None
        if mark and int(mark.group(3)) == limit.minutes:
            limit.shortened_on, limit.shortened_from = mark.group(1), int(mark.group(2))
    limits.sort(key=lambda l: l.line)
    return limits


def job_name_matches(pattern: str, api_name: str) -> bool:
    """Does an API job name belong to a workflow job?

    Matrix jobs get `` (a, b)`` appended unless the name already uses a
    matrix expression; ``${{ }}`` in the declared name matches anything.
    """
    parts = re.split(r"\$\{\{.*?\}\}", pattern)
    regex = ".*?".join(re.escape(p) for p in parts)
    return re.fullmatch(regex + r"( \(.*\))?", api_name) is not None


# ---------------------------------------------------------------------------
# Samples from the Actions API
# ---------------------------------------------------------------------------

def _ts(value: str | None) -> float | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


@dataclass
class Sample:
    run_id: int
    attempt: int
    job_id: int
    event: str
    branch: str
    created: str
    conclusion: str
    seconds: float
    started: float
    completed: float
    # for a job-level limit: the job's longest step, used to measure setup
    longest_step_seconds: float = 0.0
    step_seconds: dict = field(default_factory=dict)
    killed: bool = False
    kill: dict | None = None     # classification, filled for kills only

    @property
    def on_main(self) -> bool:
        return self.event == "push" and self.branch == "main"


def samples_for_limit(limit: Limit, runs: list[dict], jobs_by_run: dict[int, list[dict]],
                      th: Thresholds = THRESHOLDS) -> list[Sample]:
    """One sample per run attempt whose job (and step) ran to an end."""
    out: list[Sample] = []
    wall = limit.minutes * 60
    for run in runs:
        for job in jobs_by_run.get(run["id"], []):
            if not job_name_matches(limit.job_name, job.get("name", "")):
                continue
            if job.get("status") != "completed" or job.get("conclusion") in (None, "skipped"):
                continue
            steps = job.get("steps") or []
            step_secs = {}
            for s in steps:
                a, b = _ts(s.get("started_at")), _ts(s.get("completed_at"))
                if a is not None and b is not None and b >= a and s.get("conclusion") != "skipped":
                    step_secs[s["name"]] = b - a
            if limit.step_name:
                step = next((s for s in steps if s.get("name") == limit.step_name), None)
                if not step or step.get("conclusion") in (None, "skipped"):
                    continue
                a, b, conclusion = _ts(step.get("started_at")), _ts(step.get("completed_at")), step["conclusion"]
            else:
                a, b, conclusion = _ts(job.get("started_at")), _ts(job.get("completed_at")), job["conclusion"]
            if a is None or b is None or b < a:
                continue
            secs = b - a
            out.append(Sample(
                run_id=run["id"], attempt=job.get("run_attempt", 1), job_id=job["id"],
                event=run.get("event", ""), branch=run.get("head_branch", ""),
                created=run.get("created_at", ""), conclusion=conclusion, seconds=secs,
                started=a, completed=b,
                longest_step_seconds=max(step_secs.values(), default=0.0),
                step_seconds=step_secs,
                killed=conclusion in ("failure", "cancelled", "timed_out") and secs >= wall - th.kill_slack_seconds,
            ))
    return out


# ---------------------------------------------------------------------------
# Log reading: kill classification and per-file timing
# ---------------------------------------------------------------------------

_LOG_TS_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z ?(.*)$")
_PROGRESS_RE = re.compile(r"\[\s*(\d{1,3})%\]")
_FAULTHANDLER_RE = re.compile(r"^Timeout \(\d+:\d\d:\d\d\)!")
_PYTEST_TIMEOUT_RE = re.compile(r"^\+{5,} Timeout \+{5,}")
_WORKER_CRASH_RE = re.compile(r"worker '?(gw\d+)'? crashed while running '?([^'\s]+::[^'\s]+)'?")
_FRAME_RE = re.compile(r'File "[^"]*?/?(tests/[^"]+\.py)", line \d+ in (\w+)')
_RESULT_XDIST_RE = re.compile(r"^\[(gw\d+)\] \[\s*\d{1,3}%\] (?:PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS) (\S+?)::")
_RESULT_PLAIN_RE = re.compile(r"^(\S+?\.py)::\S+ (?:PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\b.*\[\s*\d{1,3}%\]")
_STARTED_RE = re.compile(r"^(tests/\S+?\.py::\S+)\s*$")
_FINISHED_RE = re.compile(r"(?:PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS) (tests/\S+?\.py::\S+)")
_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)s (call|setup|teardown)\s+(\S+)")
_NETWORK_RE = re.compile(
    r"Unable to connect|Could not resolve|Temporary failure resolving|Connection (?:timed out|refused|reset)"
    r"|ETIMEDOUT|ECONNRESET|ECONNREFUSED|EAI_AGAIN|socket hang up|^(?:Err|Ign):\d+|[Rr]etrying")
_FAILURE_RE = re.compile(r"(?:^|\] )(?:FAILED|ERROR) \S|^##\[error\]|✘")
_RESULT_WORD_RE = re.compile(r"\b(?:PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\b")


def log_lines(text: str, start: float | None = None, end: float | None = None) -> list[tuple[float, str]]:
    """(timestamp, message) for each log line, optionally inside a window.

    GitHub prefixes every line with an ISO timestamp; a step's lines are the
    ones between its started_at and completed_at (whole seconds, so the
    window is padded by a second on each side).
    """
    out = []
    last_ts = None
    for raw in text.splitlines():
        m = _LOG_TS_RE.match(raw)
        if m:
            base = dt.datetime.fromisoformat(m.group(1) + "+00:00").timestamp()
            frac = float("0." + m.group(2)) if m.group(2) else 0.0
            last_ts = base + frac
            msg = m.group(3)
        elif last_ts is None:
            continue
        else:
            msg = raw
        if start is not None and last_ts < start - 1:
            continue
        if end is not None and last_ts > end + 1:
            continue
        out.append((last_ts, msg))
    return out


def classify_kill(text: str, start: float | None = None, end: float | None = None,
                  th: Thresholds = THRESHOLDS) -> dict:
    """Was the run still advancing when the wall arrived, or stuck?

    kind is one of:

    * ``stuck`` - a faulthandler or pytest-timeout dump, a crashed xdist
      worker (cause ``dump``), no progress for ``stall_seconds`` before the
      kill (``silent``), or a tail of network errors (``network``).  A longer
      limit would only have waited longer.
    * ``failing`` - still advancing, but tests were failing: the run was red
      anyway, so the limit is not what to change.
    * ``grew`` - still advancing with nothing failing: the work outgrew the
      limit.
    * ``unknown`` - no log lines in the window (expired or never written).

    Progress means pytest result lines when the step prints any, otherwise
    any output line (an install step has no percentages).
    """
    lines = log_lines(text, start, end)
    kill_at = end if end is not None else (lines[-1][0] if lines else 0.0)
    output: list[tuple[float, str]] = []
    results: list[tuple[float, str]] = []
    last_pct = None
    dump_at = None
    stuck_test = None
    failures = 0
    started: dict[str, float] = {}
    finished: set[str] = set()
    for i, (ts, msg) in enumerate(lines):
        if msg.startswith("##[error]The action") or msg.startswith("##[error]The operation was canceled") \
                or msg.startswith("##[error]The job running"):
            kill_at = ts if end is None else min(kill_at, ts)
            break     # the wall: what follows belongs to later steps
        if _FAILURE_RE.search(msg):
            failures += 1
        if msg.startswith("##["):
            continue
        output.append((ts, msg))
        pm = _PROGRESS_RE.search(msg)
        if pm and _RESULT_WORD_RE.search(msg):
            last_pct = int(pm.group(1))
            results.append((ts, msg))
        sm = _STARTED_RE.match(msg)
        if sm:
            started.setdefault(sm.group(1), ts)
        fm = _FINISHED_RE.search(msg)
        if fm:
            finished.add(fm.group(1))
        wm = _WORKER_CRASH_RE.search(msg)
        if wm and dump_at is None:
            dump_at = ts
            stuck_test = wm.group(2)
        if dump_at is None and (_FAULTHANDLER_RE.match(msg) or _PYTEST_TIMEOUT_RE.match(msg)):
            dump_at = ts
            frames = []
            for _, follow in lines[i + 1:i + 400]:
                fr = _FRAME_RE.search(follow)
                if fr:
                    frames.append((fr.group(1), fr.group(2)))
            test_frames = [f for f in frames if f[1].startswith("test")]
            pick = (test_frames or frames or [None])[0]
            if pick:
                stuck_test = f"{pick[0]}::{pick[1]}"
    progress = results or [(ts, m) for ts, m in output if not _NETWORK_RE.search(m)]
    last_progress_at = progress[-1][0] if progress else None
    silence = (kill_at - last_progress_at) if last_progress_at is not None else None
    tail = [m for ts, m in output if ts >= kill_at - th.stall_seconds]
    network = sum(1 for m in tail if _NETWORK_RE.search(m))
    cause = None
    if dump_at is not None:
        kind, cause = "stuck", "dump"
    elif not results and tail and network >= th.network_min_lines and network / len(tail) >= th.network_share:
        kind, cause = "stuck", "network"
    elif silence is not None and silence >= th.stall_seconds:
        kind, cause = "stuck", "silent"
    elif not output:
        kind = "unknown"
    elif failures:
        kind = "failing"
    else:
        kind = "grew"
    unfinished = sorted((t for t in started if t not in finished), key=lambda t: started[t])
    if kind == "stuck" and not stuck_test and unfinished:
        stuck_test = unfinished[-1]
    return {
        "kind": kind,
        "cause": cause,
        "last_progress_pct": last_pct,
        "silence_seconds": round(silence, 1) if silence is not None else None,
        "failures": failures,
        "stuck_test": stuck_test,
    }


def per_file_seconds(text: str, start: float | None = None, end: float | None = None) -> dict[str, float]:
    """Seconds each test file took, from result-line timestamps.

    Under ``--dist=loadfile`` a file runs on one worker, so a file's time is
    from the previous result on its worker to its own last result.  Without
    xdist the whole step is one worker.  Falls back to the ``--durations``
    block (summed per file) when no per-test result lines exist.
    """
    lines = log_lines(text, start, end)
    last_on_worker: dict[str, float] = {}
    file_worker: dict[str, str] = {}
    first_ts = lines[0][0] if lines else 0.0
    spans: dict[str, list[float]] = {}
    for ts, msg in lines:
        m = _RESULT_XDIST_RE.match(msg)
        if m:
            worker, path = m.group(1), m.group(2)
        else:
            m = _RESULT_PLAIN_RE.match(msg)
            if not m:
                continue
            worker, path = "main", m.group(1)
        if path not in spans:
            spans[path] = [last_on_worker.get(worker, first_ts), ts]
            file_worker[path] = worker
        else:
            spans[path][1] = ts
        last_on_worker[worker] = ts
    if spans:
        return {p: max(0.0, b - a) for p, (a, b) in spans.items()}
    totals: dict[str, float] = {}
    for _, msg in lines:
        d = _DURATION_RE.match(msg)
        if d:
            path = d.group(3).split("::", 1)[0]
            totals[path] = totals.get(path, 0.0) + float(d.group(1))
    return totals


def growth_by_file(old: dict[str, float], new: dict[str, float], th: Thresholds = THRESHOLDS) -> dict:
    """Which files added the time between an older and a newer run."""
    deltas = {p: new.get(p, 0.0) - old.get(p, 0.0) for p in set(old) | set(new)}
    grown = sorted(((p, d) for p, d in deltas.items() if d > 0), key=lambda x: -x[1])
    total = sum(d for _, d in grown)
    top = grown[:th.concentrated_top_files]
    share = (sum(d for _, d in top) / total) if total else 0.0
    return {
        "added_seconds": round(total, 1),
        "top": [{"file": p, "old_s": round(old.get(p, 0.0), 1), "new_s": round(new.get(p, 0.0), 1),
                 "delta_s": round(d, 1)} for p, d in grown[:5]],
        "top_share": round(share, 2),
        "concentrated": total >= th.concentrated_min_seconds and share >= th.concentrated_share,
        "new_files": sum(1 for p in new if p not in old),
    }


# ---------------------------------------------------------------------------
# Judging
# ---------------------------------------------------------------------------

def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100); no interpolation surprises."""
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def round_up(minutes: float, step: int) -> int:
    return max(step, int(math.ceil(minutes / step - 1e-9) * step))


@dataclass
class Verdict:
    key: str
    scope: str
    current: int
    verdict: str                  # ok | raise | shorten | fix-code
    also: list[str]
    recommended: int | None
    reason: str
    runs: int
    source: str                   # main | main+pr
    p50: float | None
    p90: float | None
    p95: float | None
    max: float | None
    kills: list[dict]
    growth: dict | None = None
    note: str = ""


def judge(limit: Limit, samples: list[Sample], growth: dict | None = None,
          th: Thresholds = THRESHOLDS, today: dt.date | None = None) -> Verdict:
    """One verdict for one limit, with the numbers behind it."""
    healthy_main = [s for s in samples if s.on_main and s.conclusion == "success"]
    healthy = healthy_main
    source = "main"
    if len(healthy_main) < 10:
        healthy = [s for s in samples if s.conclusion == "success"]
        source = "main+pr"
    healthy.sort(key=lambda s: s.started)
    mins = [s.seconds / 60 for s in healthy]
    kills = [s for s in samples if s.killed]
    kill_info = [dict(run_id=s.run_id, attempt=s.attempt, job_id=s.job_id, branch=s.branch,
                      **(s.kill or {"kind": "unknown"})) for s in kills]
    p50 = statistics.median(mins) if mins else None
    for k in kill_info:
        if k["kind"] == "grew" and p50 and limit.minutes > th.outlier_factor * p50:
            k["kind"], k["cause"] = "stuck", "outlier"
    advancing = [k for k in kill_info if k["kind"] == "grew" or k.get("cause") == "outlier"]
    today = today or dt.date.today()
    regret = bool(limit.shortened_on and advancing and limit.shortened_from
                  and (today - dt.date.fromisoformat(limit.shortened_on)).days <= th.shorten_regret_days)
    grew_kills = [k for k in kill_info if k["kind"] == "grew"]
    stuck_kills = [k for k in kill_info if k["kind"] == "stuck"]
    failing_kills = [k for k in kill_info if k["kind"] == "failing"]
    grown = bool(growth and (growth.get("window_growth") or 0) > th.growth_fraction)
    p90 = percentile(mins, 90) if mins else None
    p95 = percentile(mins, 95) if mins else None
    mx = max(mins) if mins else None
    enough = len(mins) >= th.judge_min_runs
    reasons: dict[str, str] = {}

    if grew_kills:
        reasons["raise"] = f"{len(grew_kills)} kill(s) while tests were still advancing"
    if enough and p90 is not None and p90 > th.raise_p90_fraction * limit.minutes:
        reasons.setdefault("raise", f"p90 {p90:.1f} min is {p90 / limit.minutes:.0%} of the {limit.minutes}-min limit")
    if stuck_kills:
        named = ", ".join(f"{n} x{c}" if c > 1 else n for n, c in sorted(
            collections.Counter(k["stuck_test"] or k["cause"] for k in stuck_kills).items()))
        reasons["fix-code"] = f"{len(stuck_kills)} stuck kill(s): {named}"
    if growth and growth.get("concentrated"):
        files = ", ".join(t["file"] for t in growth["top"][:th.concentrated_top_files])
        reasons.setdefault("fix-code", f"growth concentrated in {files}")
    if (not kills and not grown and not limit.pinned and len(mins) >= th.shorten_min_runs
            and p95 is not None and p95 < th.shorten_p95_fraction * limit.minutes):
        reasons["shorten"] = f"p95 {p95:.1f} min is {p95 / limit.minutes:.0%} of the {limit.minutes}-min limit"

    # A limit whose only kills were stuck runs needs the code fixed first:
    # raising it would only make the same hang wait longer.
    if regret:
        reasons = {"raise": f"{len(advancing)} advancing kill(s) after this tool shortened it on "
                            f"{limit.shortened_on}; restoring {limit.shortened_from}"}
    order = ["raise", "fix-code", "shorten"] if grew_kills or regret else ["fix-code", "raise", "shorten"]
    found = [v for v in order if v in reasons]
    verdict = found[0] if found else "ok"
    recommended = None
    if verdict == "raise" and regret:
        recommended = max(limit.shortened_from, limit.minutes + th.round_to_minutes)
    elif verdict == "raise":
        base = max([p95 or 0.0] + ([float(limit.minutes)] if grew_kills else []))
        recommended = max(round_up(th.headroom * base, th.round_to_minutes),
                          limit.minutes + th.round_to_minutes)
    elif verdict == "shorten":
        recommended = max(round_up(th.headroom * mx, th.round_to_minutes),
                          round_up(limit.minutes * th.shorten_floor_fraction, th.round_to_minutes))
        if recommended >= limit.minutes:
            verdict, recommended = "ok", None
            reasons.pop("shorten")
            found = [v for v in found if v != "shorten"]
    notes = [] if enough else [f"only {len(mins)} healthy run(s); not judged on percentiles"]
    if failing_kills:
        notes.append(f"{len(failing_kills)} kill(s) while tests were failing (red anyway; not a limit signal)")
    if limit.pinned:
        notes.append(f"pinned: {limit.pinned}")
    note = "; ".join(notes)
    if growth and growth.get("window_growth") is not None:
        note = (note + "; " if note else "") + f"median grew {growth['window_growth']:+.0%} across the window"
    return Verdict(
        key=limit.key, scope=limit.scope, current=limit.minutes, verdict=verdict,
        also=found[1:], recommended=recommended,
        reason=reasons.get(verdict, "within thresholds") + "".join(f"; also {v}: {reasons[v]}" for v in found[1:]),
        runs=len(mins), source=source,
        p50=_r(p50), p90=_r(p90), p95=_r(p95), max=_r(mx), kills=kill_info, growth=growth, note=note,
    )


def _r(x):
    return round(x, 1) if x is not None else None


def window_growth(samples: list[Sample], th: Thresholds = THRESHOLDS) -> tuple[float | None, Sample | None, Sample | None]:
    """Median of the newer half over the older half, minus one.

    Returns the ratio and the oldest/newest healthy main samples to compare.
    """
    healthy = sorted((s for s in samples if s.on_main and s.conclusion == "success"), key=lambda s: s.started)
    if len(healthy) < th.growth_min_runs:
        return None, None, None
    half = len(healthy) // 2
    old = statistics.median(s.seconds for s in healthy[:half])
    new = statistics.median(s.seconds for s in healthy[half:])
    return (new / old - 1) if old else None, healthy[0], healthy[-1]


def reconcile_job_limits(limits: list[Limit], verdicts: dict[str, Verdict],
                         samples: dict[str, list[Sample]], th: Thresholds = THRESHOLDS) -> None:
    """Keep each job limit above its step limits plus the job's setup time."""
    for job_limit in (l for l in limits if l.scope == "job"):
        steps = [l for l in limits if l.scope == "step" and l.workflow == job_limit.workflow
                 and l.job_id == job_limit.job_id]
        if not steps:
            continue
        jv = verdicts[job_limit.key]
        job_now = jv.recommended or job_limit.minutes
        for st in steps:
            sv = verdicts[st.key]
            step_limit = sv.recommended or st.minutes
            setups = [(s.seconds - s.step_seconds.get(st.step_name, s.longest_step_seconds)) / 60
                      for s in samples[job_limit.key] if s.conclusion == "success"]
            setup = percentile(setups, 95) if setups else 5.0
            needed = round_up(step_limit + th.headroom * max(setup, 1.0), th.round_to_minutes)
            if job_now < needed:
                was = jv.verdict
                job_now = needed
                jv.recommended = needed
                why = (f"keeps the job above its step '{st.step_name}' ({step_limit} min) "
                       f"plus setup (p95 {setup:.1f} min)")
                if was in ("ok", "shorten"):
                    jv.verdict, jv.reason = "raise", why
                else:
                    jv.reason += f"; {why}"


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------

def apply_changes(text: str, changes: list[tuple[Limit, int, str]]) -> str:
    """Rewrite each limit's value and the ``# ci-time-budget`` line above it.

    Touches only the ``timeout-minutes`` number (an inline comment after it is
    kept) and one marker comment directly above; an existing marker there is
    replaced, never stacked.  Everything else stays byte-identical.
    """
    keep_newline = text.endswith("\n")
    lines = text.split("\n")
    for limit, minutes, comment in sorted(changes, key=lambda c: -c[0].line):
        raw = lines[limit.line]
        m = _TIMEOUT_RE.match(raw)
        if not m or int(m.group("value")) != limit.minutes:
            raise ValueError(f"{limit.key}: line {limit.line + 1} is not 'timeout-minutes: {limit.minutes}'")
        lines[limit.line] = raw[:m.start("value")] + str(minutes) + raw[m.end("value"):]
        marker = f"{m.group('indent')}{MARKER} {comment}"
        prev = limit.line - 1
        if prev >= 0 and lines[prev].strip().startswith(MARKER):
            lines[prev] = marker
        else:
            lines.insert(limit.line, marker)
    out = "\n".join(lines)
    if keep_newline and not out.endswith("\n"):
        out += "\n"
    return out


def change_comment(v: Verdict, today: str) -> str:
    stats = f"p50 {v.p50}, p95 {v.p95}, max {v.max} min over {v.runs} {v.source} runs" if v.runs else "no healthy runs"
    return f"{today}: {v.verdict} {v.current} -> {v.recommended} min; {stats}; {v.reason}."


# ---------------------------------------------------------------------------
# GitHub access
# ---------------------------------------------------------------------------

class GitHub:
    def __init__(self, repo: str, gh: str = "gh"):
        self.repo = repo
        self.gh = gh.split()

    def _run(self, path: str) -> str:
        proc = subprocess.run([*self.gh, "api", path], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"gh api {path} failed: {proc.stderr.strip()[:300]}")
        return proc.stdout

    def json(self, path: str):
        return json.loads(self._run(f"repos/{self.repo}/{path}"))

    def log(self, job_id: int) -> str:
        try:
            return self._run(f"repos/{self.repo}/actions/jobs/{job_id}/logs")
        except RuntimeError:
            return ""     # expired or not yet available


def fetch_runs(gh: GitHub, workflow: str, n: int) -> list[dict]:
    """Last n completed main runs, then last n completed PR runs."""
    seen: dict[int, dict] = {}
    for query in (f"branch=main&event=push", "event=pull_request"):
        data = gh.json(f"actions/workflows/{workflow}/runs?status=completed&per_page={n}&{query}")
        for run in data.get("workflow_runs", []):
            seen.setdefault(run["id"], run)
    return list(seen.values())


def fetch_jobs(gh: GitHub, runs: list[dict], workers: int = 6) -> dict[int, list[dict]]:
    def one(run):
        return run["id"], gh.json(f"actions/runs/{run['id']}/jobs?filter=all&per_page=100").get("jobs", [])
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(one, runs))


# ---------------------------------------------------------------------------
# Orchestration and output
# ---------------------------------------------------------------------------

def evaluate(workflows: dict[str, str], runs_by_wf: dict[str, list[dict]],
             jobs_by_wf: dict[str, dict[int, list[dict]]], get_log: Callable[[int], str],
             th: Thresholds = THRESHOLDS) -> tuple[list[Limit], dict[str, Verdict]]:
    """Judge every limit.  Pure apart from ``get_log`` (job id -> log text)."""
    limits: list[Limit] = []
    for name, text in workflows.items():
        limits.extend(parse_workflow(text, name))
    verdicts: dict[str, Verdict] = {}
    samples_by_key: dict[str, list[Sample]] = {}
    logs: dict[int, str] = {}

    def log_for(job_id):
        if job_id not in logs:
            logs[job_id] = get_log(job_id)
        return logs[job_id]

    for limit in limits:
        samples = samples_for_limit(limit, runs_by_wf.get(limit.workflow, []),
                                    jobs_by_wf.get(limit.workflow, {}), th)
        samples_by_key[limit.key] = samples
        for s in samples:
            if s.killed:
                s.kill = classify_kill(log_for(s.job_id), s.started, s.completed, th)
        growth = None
        ratio, oldest, newest = window_growth(samples, th)
        if ratio is not None and ratio > th.growth_fraction and limit.scope == "step":
            growth = growth_by_file(per_file_seconds(log_for(oldest.job_id), oldest.started, oldest.completed),
                                    per_file_seconds(log_for(newest.job_id), newest.started, newest.completed), th)
            growth["window_growth"] = round(ratio, 3)
            growth["compared_runs"] = [oldest.run_id, newest.run_id]
        elif ratio is not None:
            growth = {"window_growth": round(ratio, 3), "concentrated": False}
        verdicts[limit.key] = judge(limit, samples, growth, th)
    reconcile_job_limits(limits, verdicts, samples_by_key, th)
    return limits, verdicts


def render_markdown(verdicts: Iterable[Verdict]) -> str:
    rows = ["| limit | now | verdict | new | runs | p50 | p90 | p95 | max | kills | reason |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    detail = []
    for v in verdicts:
        kills = ", ".join(f"{k['kind']}" + (f"({k['cause']})" if k.get("cause") else "")
                          + (f"@{k['last_progress_pct']}%" if k.get("last_progress_pct") is not None else "")
                          for k in v.kills) or "-"
        rows.append(f"| {v.key} ({v.scope}) | {v.current} | **{v.verdict}** | {v.recommended or '-'} | "
                    f"{v.runs} {v.source} | {v.p50 or '-'} | {v.p90 or '-'} | {v.p95 or '-'} | {v.max or '-'} | "
                    f"{kills} | {v.reason}{(' (' + v.note + ')') if v.note else ''} |")
        if v.growth and v.growth.get("top"):
            tops = "; ".join(f"{t['file']} {t['old_s']}s -> {t['new_s']}s" for t in v.growth["top"])
            detail.append(f"- {v.key}: grew {v.growth['window_growth']:+.0%}; added {v.growth['added_seconds']}s "
                          f"across files (top {THRESHOLDS.concentrated_top_files} carry {v.growth['top_share']:.0%}, "
                          f"{v.growth['new_files']} new files): {tops}")
        for k in v.kills:
            if k.get("stuck_test"):
                detail.append(f"- {v.key}: run {k['run_id']} attempt {k['attempt']} stuck in {k['stuck_test']}")
    return "\n".join(rows + ([""] + detail if detail else []))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", required=True, help="OWNER/NAME on GitHub")
    ap.add_argument("--root", default=".", help="repository checkout holding .github/workflows")
    ap.add_argument("--runs", type=int, default=50, help="completed runs per source (main, PR) per workflow")
    ap.add_argument("--workflow", action="append", help="only these workflow files (repeatable)")
    ap.add_argument("--gh", default="gh", help="gh binary (or wrapper) to call")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a markdown table")
    ap.add_argument("--apply", action="store_true", help="rewrite the recommended raise/shorten limits")
    ap.add_argument("--only-actionable", action="store_true", help="omit ok verdicts from the output")
    args = ap.parse_args(argv)

    wf_dir = Path(args.root) / ".github" / "workflows"
    files = sorted(p for p in wf_dir.glob("*.y*ml") if not args.workflow or p.name in args.workflow)
    workflows = {p.name: p.read_text() for p in files}
    workflows = {k: v for k, v in workflows.items() if parse_workflow(v, k)}
    gh = GitHub(args.repo, args.gh)
    runs_by_wf = {name: fetch_runs(gh, name, args.runs) for name in workflows}
    jobs_by_wf = {name: fetch_jobs(gh, runs) for name, runs in runs_by_wf.items()}
    limits, verdicts = evaluate(workflows, runs_by_wf, jobs_by_wf, gh.log)
    shown = [v for v in verdicts.values() if not args.only_actionable or v.verdict != "ok"]
    if args.json:
        print(json.dumps([asdict(v) for v in shown], indent=2))
    else:
        print(render_markdown(shown) if shown else "All CI time budgets ok.")
    if args.apply:
        today = dt.date.today().isoformat()
        by_file: dict[str, list] = {}
        for limit in limits:
            v = verdicts[limit.key]
            if v.verdict in ("raise", "shorten") and v.recommended and v.recommended != limit.minutes:
                by_file.setdefault(limit.workflow, []).append((limit, v.recommended, change_comment(v, today)))
        for name, changes in by_file.items():
            path = wf_dir / name
            path.write_text(apply_changes(path.read_text(), changes))
            print(f"applied {len(changes)} change(s) to {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
