"""The README progress tracker counts fix attempts the way its note says.

Each test builds a tiny GitHub cache by hand and checks the outcome counts
that would land on the chart.
"""
import importlib.util
from collections import Counter
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "progress_tracker.py"


@pytest.fixture(scope="module")
def pt():
    spec = importlib.util.spec_from_file_location("progress_tracker", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def user(name):
    return {"login": name}


def comment(who, at, body, n, reactions=()):
    return {"id": f"c{n}", "url": f"https://example.test/c{n}", "author": user(who), "createdAt": at, "body": body,
            "reactions": {"nodes": [{"content": r, "user": user(u)} for u, r in reactions]}}


def issue(number, author, comments=(), events=()):
    return {"number": number, "title": f"issue {number}", "url": f"https://example.test/issues/{number}",
            "author": user(author), "body": "it is broken", "createdAt": "2026-06-01T00:00:00Z",
            "comments": {"nodes": list(comments)}, "timelineItems": {"nodes": list(events)}}


def closed(at, who="new-usemame", reason="COMPLETED"):
    return {"__typename": "ClosedEvent", "createdAt": at, "actor": user(who), "stateReason": reason}


def reopened(at, who):
    return {"__typename": "ReopenedEvent", "createdAt": at, "actor": user(who)}


def pr(number, title, merged_at, author="new-usemame", body=""):
    return {"number": number, "title": title, "body": body, "url": f"https://example.test/pull/{number}",
            "author": user(author), "createdAt": "2026-06-01T00:00:00Z", "mergedAt": merged_at,
            "closingIssuesReferences": {"nodes": []}, "comments": {"nodes": []},
            "timelineItems": {"nodes": [{"__typename": "MergedEvent", "createdAt": merged_at, "actor": user("new-usemame")}]
                              if merged_at else []}}


def outcomes(pt, issues=(), prs=(), upstream=(), verdicts=None):
    cache = {"fetched_at": "2026-10-20T00:00:00Z", "issues": list(issues), "prs": list(prs), "upstream": list(upstream)}
    return Counter(a["outcome"] for a in pt.resolved_attempts(cache, verdicts or {}))


def test_every_close_in_a_reopen_cycle_counts(pt):
    events = []
    for k in range(4):
        events += [closed(f"2026-06-0{2 * k + 1}T00:00:00Z"), reopened(f"2026-06-0{2 * k + 2}T00:00:00Z", "reporter")]
    assert outcomes(pt, [issue(1, "reporter", events=events)]) == {"not_fixed": 4}


def test_quiet_deliveries_count_once_and_a_person_close_is_happy(pt):
    prs = [pr(10, "fix: thing (#1)", "2026-06-02T00:00:00Z"), pr(11, "fix: rest of thing, fixes #1", "2026-06-03T00:00:00Z")]
    got = outcomes(pt, [issue(1, "reporter", events=[closed("2026-06-05T00:00:00Z", "reporter")])], prs)
    assert got == {"happy": 1}


def test_a_close_we_took_back_before_anyone_answered_is_not_a_delivery(pt):
    events = [closed("2026-06-02T00:00:00Z"), reopened("2026-06-10T00:00:00Z", "new-usemame")]
    assert outcomes(pt, [issue(1, "reporter", events=events)]) == {}


def test_upstream_numbers_in_a_title_are_not_fork_issues(pt):
    prs = [pr(10, "fix: gate checksum write (CWA #1)", "2026-06-02T00:00:00Z"),
           pr(11, "fix: kobo fold (Greptile #1 P1)", "2026-06-02T00:00:00Z"),
           pr(12, "fix: IDOR (#1 upstream)", "2026-06-02T00:00:00Z")]
    assert outcomes(pt, [issue(1, "reporter")], prs) == {}


def test_a_reply_needs_a_verdict_and_the_verdict_decides(pt):
    reply = comment("reporter", "2026-06-03T00:00:00Z", "still broken on 4.1.2", 1)
    thread = issue(1, "reporter", comments=[reply], events=[closed("2026-06-02T00:00:00Z")])
    assert outcomes(pt, [thread]) == {"unread": 1}
    attempt = pt.resolved_attempts({"fetched_at": "", "issues": [thread], "prs": [], "upstream": []}, {})[0]
    verdicts = {attempt["id"]: {"hash": attempt["hash"], "verdict": "not_fixed"}}
    assert outcomes(pt, [thread], verdicts=verdicts) == {"not_fixed": 1}
    # A new reply changes the window, so the old verdict no longer applies.
    thread["comments"]["nodes"].append(comment("reporter", "2026-06-04T00:00:00Z", "oh wait, works now", 2))
    assert outcomes(pt, [thread], verdicts=verdicts) == {"unread": 1}


def test_upstream_note_pointing_at_the_fork_is_a_delivery_and_a_thumbs_up_from_the_reporter_is_happy(pt):
    note = comment("new-usemame", "2026-06-02T00:00:00Z",
                   "Fixed in https://github.com/new-usemame/Calibre-Web-NextGen", 1,
                   reactions=[("reporter", "THUMBS_UP"), ("bystander", "THUMBS_DOWN")])
    thread = {"repo": "crocodilestick/Calibre-Web-Automated", "number": 5, "title": "t",
              "url": "https://example.test/u/5", "author": user("reporter"), "body": "",
              "comments": {"nodes": [comment("new-usemame", "2026-06-01T00:00:00Z", "Could you share logs?", 0), note]}}
    assert outcomes(pt, upstream=[thread]) == {"happy": 1}
    # Asking for logs upstream delivers nothing, whatever the reporter says next.
    thread["comments"]["nodes"] = [comment("new-usemame", "2026-06-01T00:00:00Z", "Could you share logs?", 0),
                                   comment("reporter", "2026-06-02T00:00:00Z", "here you go", 3)]
    assert outcomes(pt, upstream=[thread]) == {}


def test_a_judged_reply_outranks_the_pushback_label(pt):
    # The person pushed back, then found it was their own network: their last word decides.
    comments = [comment("reporter", "2026-06-03T00:00:00Z", "still broken", 1),
                comment("reporter", "2026-06-04T00:00:00Z", "my VPN was the problem, works now", 2)]
    events = [closed("2026-06-02T00:00:00Z"),
              {"__typename": "LabeledEvent", "createdAt": "2026-06-03T01:00:00Z", "label": {"name": "pushback"}}]
    thread = issue(1, "reporter", comments=comments, events=events)
    attempt = pt.resolved_attempts({"fetched_at": "", "issues": [thread], "prs": [], "upstream": []}, {})[0]
    assert outcomes(pt, [thread]) == {"unread": 1}
    fixed = {attempt["id"]: {"hash": attempt["hash"], "verdict": "fixed"}}
    assert outcomes(pt, [thread], verdicts=fixed) == {"happy": 1}
    silent = {attempt["id"]: {"hash": attempt["hash"], "verdict": "none"}}
    assert outcomes(pt, [thread], verdicts=silent) == {"not_fixed": 1}


def test_reopening_your_own_close_is_not_a_failed_fix(pt):
    prs = [pr(10, "fix: thing (#1)", "2026-06-01T00:00:00Z")]
    own = [closed("2026-06-02T00:00:00Z", "reporter"), reopened("2026-06-04T00:00:00Z", "reporter")]
    assert outcomes(pt, [issue(1, "reporter", events=own + [closed("2026-06-05T00:00:00Z", "reporter")])], prs) == {"happy": 1}
    # Reopened and left open: their earlier close no longer says they were happy.
    assert outcomes(pt, [issue(1, "reporter", events=own)], prs) == {"no_reply": 1}
    # Reopening our close still counts against the fix.
    ours = [closed("2026-06-02T00:00:00Z"), reopened("2026-06-04T00:00:00Z", "reporter")]
    assert outcomes(pt, [issue(1, "reporter", events=ours)]) == {"not_fixed": 1}


def test_the_open_count_is_what_is_open_when_fetched(pt):
    # A PR opened late on the last day is open now, though the day's average barely counts it.
    prs = [pr(10, "a", None), pr(11, "b", None)]
    prs[1]["createdAt"] = "2026-06-02T09:00:00Z"
    cache = {"fetched_at": "2026-06-02T12:00:00Z", "issues": [], "prs": prs, "upstream": []}
    rows = pt.throughput(cache)
    block = pt.readme_block(rows, [], cache, rows[-1]["day"], 0)
    assert "2 pull requests were open" in block
    assert "Pull request throughput: 2 open" in block
