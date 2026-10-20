#!/usr/bin/env python3
"""Build the README's progress tracker from this repo's GitHub history.

Two charts:

  * Throughput: open pull requests (daily average) and pull requests closed
    (running total, merged or not).
  * Fix outcomes: every time we told someone their problem was dealt with,
    what they said next. Happy (it works, or they were content), Not fixed
    (reopened, still broken in whole or part, or unhappy with how it was
    handled), or No reply.

A "fix attempt" is the moment a person was told their thread was done: a fix
PR merged for their issue, their issue closed as completed, their own PR
merged or closed, or our comment on an upstream CWA/CW thread pointing them at
the fork. Deliveries that land before anyone replies count once. An attempt
that drew no answer before the next one landed is folded into the next one,
so four closes in a thread with four complaints count four not-fixed attempts,
while two quiet follow-up PRs count as one.

Reading a reply takes judgment, so each reply window is judged once by a model
and stored in ``docs/progress-tracker/verdicts.json`` with the quote it rests
on; every label can be audited and corrected by hand. Reopens by the person,
the ``pushback`` label, reactions and the person closing their own thread are
read here without a model.

Subcommands (stdlib only; ``gh`` must be authenticated):

  fetch    pull PRs, issues and our upstream threads into build/progress-tracker
  queue    write the reply windows that still need a verdict as JSONL
  apply    merge a JSONL of verdicts into verdicts.json
  render   write the SVG charts, the README block and attempts.json

    python3 scripts/progress_tracker.py fetch
    python3 scripts/progress_tracker.py queue --out queue.jsonl
    python3 scripts/progress_tracker.py apply judged.jsonl --by <model>
    python3 scripts/progress_tracker.py render
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import Counter
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "new-usemame/Calibre-Web-NextGen"
UPSTREAMS = ("crocodilestick/Calibre-Web-Automated", "janeczku/calibre-web")
SELF = "new-usemame"
BOTS = {"github-actions", "dependabot", "greptile-apps", "coderabbitai", "copilot-pull-request-reviewer",
        "Copilot", "codecov", "sonarqubecloud", "stale", "renovate", "allcontributors"}
POSITIVE_REACTIONS = {"THUMBS_UP", "HEART", "HOORAY", "ROCKET"}
NEGATIVE_REACTIONS = {"THUMBS_DOWN", "CONFUSED"}
HAPPY_VERDICTS = {"fixed", "content"}
BAD_VERDICTS = {"partial", "not_fixed", "annoyed"}
VERDICTS_ALLOWED = HAPPY_VERDICTS | BAD_VERDICTS | {"none"}
# Replies this recent may still change their verdict; the chart dashes them.
SETTLING = dt.timedelta(days=14)

CACHE = ROOT / "build" / "progress-tracker" / "github.json"
DATA = ROOT / "docs" / "progress-tracker"
VERDICTS = DATA / "verdicts.json"
ATTEMPTS = DATA / "attempts.json"
IMAGES = ROOT / "README_images"
README = ROOT / "README.md"
START = "<!-- progress-tracker:start"
END = "<!-- progress-tracker:end -->"

# A fix PR names the issue it is for with one of these verbs, or carries the
# number in a parenthetical in its title. A bare mention ("see #12") is not a
# delivery, and neither is a number qualified as upstream ("CWA #12").
TARGET_RE = re.compile(
    r"(?i)\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?|address(?:es|ed)?|ships?(?: the)? fix for|for)\b[\s:]*"
    r"((?:#\d+[\s,/&+]*(?:and\s+)?)+)")
TITLE_RE = re.compile(r"\(([^)]*#\d+[^)]*)\)")
REF_RE = re.compile(r"(?<![\w/.-])#(\d+)\b(\s+upstream)?")
QUALIFIED_RE = re.compile(r"(?i)\b(?:cwa|cw|upstream|janeczku|crocodilestick|greptile|mirrors|calibre-web)\s*$")
# Release cuts and ledger bookkeeping reference many issues without being the fix.
NOT_A_FIX_TITLE = re.compile(r"(?i)^(release prep|release:|findings:|changelog|docs\(changes?\))")
# Our upstream comment counts as a delivery when it points at the fork.
FORK_LINK_RE = re.compile(r"(?i)calibre-web-nextgen|new-usemame/")


# ---------------------------------------------------------------- fetch ----

def gh_graphql(query: str, **variables) -> dict:
    args = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        if v is not None:
            args += ["-f", f"{k}={v}"]
    last = ""
    for attempt in range(5):
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode == 0:
            return json.loads(proc.stdout)["data"]
        last = proc.stderr.strip()
        time.sleep(5 * (attempt + 1))
    sys.exit(f"gh api graphql failed after 5 tries: {last[:400]}")


def gh_rest(path: str, **params) -> dict:
    args = ["gh", "api", "-X", "GET", path] + [a for k, v in params.items() for a in ("-f", f"{k}={v}")]
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"gh api {path} failed: {proc.stderr.strip()[:400]}")
    return json.loads(proc.stdout)


def comments(window: str) -> str:
    return """comments(%s) { totalCount nodes { id url author { login } createdAt body
      reactions(first: 30) { nodes { content user { login } } } } }""" % window


def timeline(kinds: list[str]) -> str:
    frags = {
        "CLOSED_EVENT": "... on ClosedEvent { createdAt actor { login } stateReason }",
        "REOPENED_EVENT": "... on ReopenedEvent { createdAt actor { login } }",
        "MERGED_EVENT": "... on MergedEvent { createdAt actor { login } }",
        "LABELED_EVENT": "... on LabeledEvent { createdAt label { name } }",
    }
    return "timelineItems(first: 100, itemTypes: [%s]) { nodes { __typename %s } }" % (
        ", ".join(kinds), " ".join(frags[k] for k in kinds))


PR_FIELDS = """number title state createdAt closedAt mergedAt url author { login } body
  closingIssuesReferences(first: 20) { nodes { number } } %s %s""" % (
    comments("first: 100"), timeline(["CLOSED_EVENT", "REOPENED_EVENT", "MERGED_EVENT"]))
ISSUE_FIELDS = """number title state createdAt closedAt url author { login } body %s %s""" % (
    comments("first: 100"), timeline(["CLOSED_EVENT", "REOPENED_EVENT", "LABELED_EVENT"]))


def fetch_connection(kind: str, fields: str) -> list[dict]:
    owner, name = REPO.split("/")
    query = """query($cursor: String) { repository(owner: "%s", name: "%s") {
      %s(first: 25, after: $cursor, orderBy: {field: CREATED_AT, direction: ASC}) {
        pageInfo { hasNextPage endCursor } nodes { %s } } } }""" % (owner, name, kind, fields)
    nodes, cursor = [], None
    while True:
        page = gh_graphql(query, cursor=cursor)["repository"][kind]
        nodes += page["nodes"]
        print(f"  {kind}: {len(nodes)}", file=sys.stderr)
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = page["pageInfo"]["endCursor"]


def fetch_upstream_threads() -> list[dict]:
    """Every upstream issue or PR we have commented on. Popular upstream
    threads run past 100 comments, and ours are recent, so read the last 100."""
    out = []
    for repo in UPSTREAMS:
        numbers, page = [], 1
        while True:
            res = gh_rest("search/issues", q=f"commenter:{SELF} repo:{repo}", per_page=100, page=page)
            numbers += [item["number"] for item in res["items"]]
            if len(res["items"]) < 100 or page >= 10:
                break
            page += 1
        owner, name = repo.split("/")
        fields = "number title url author { login } body %s" % comments("last: 100")
        for i in range(0, len(numbers), 10):
            body = " ".join(
                "t%d: issueOrPullRequest(number: %d) { __typename ... on Issue { %s } ... on PullRequest { %s } }"
                % (n, n, fields, fields) for n in numbers[i:i + 10])
            data = gh_graphql('query { repository(owner: "%s", name: "%s") { %s } }' % (owner, name, body))
            for node in data["repository"].values():
                if node:
                    node["repo"] = repo
                    out.append(node)
        print(f"  {repo}: {len(numbers)} threads", file=sys.stderr)
    return out


def cmd_fetch(args) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "fetched_at": now_iso(),
        "prs": fetch_connection("pullRequests", PR_FIELDS),
        "issues": fetch_connection("issues", ISSUE_FIELDS),
        "upstream": fetch_upstream_threads(),
    }
    for item in data["prs"] + data["issues"]:
        # A filtered timeline's totalCount counts every event, so a full page is the tell.
        if item["comments"]["totalCount"] > len(item["comments"]["nodes"]) or \
                len(item["timelineItems"]["nodes"]) >= 100:
            print(f"warning: {item['url']} has more than 100 comments or events; the rest were not read",
                  file=sys.stderr)
    CACHE.write_text(json.dumps(data))
    print(f"cached {len(data['prs'])} PRs, {len(data['issues'])} issues and "
          f"{len(data['upstream'])} upstream threads in {CACHE.relative_to(ROOT)}")


# ------------------------------------------------------------- attempts ----

def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ts(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def login(obj) -> str:
    return ((obj or {}).get("author") or (obj or {}).get("actor") or (obj or {}).get("user") or {}).get("login") or ""


def is_outside(who: str) -> bool:
    return bool(who) and who != SELF and who not in BOTS and not who.endswith("[bot]")


def fork_refs(text: str) -> set[int]:
    found = set()
    for m in REF_RE.finditer(text):
        if m.group(2) or QUALIFIED_RE.search(text[max(0, m.start() - 18):m.start()]):
            continue
        found.add(int(m.group(1)))
    return found


def pr_targets(pr: dict, outside_issues: set[int]) -> set[int]:
    """Outside people's issues this PR says it fixes."""
    if NOT_A_FIX_TITLE.match(pr["title"]):
        return set()
    found = {n["number"] for n in pr["closingIssuesReferences"]["nodes"]}
    for m in TARGET_RE.finditer(f"{pr['title']}\n{pr['body'] or ''}"):
        found |= fork_refs(m.group(1))
    for m in TITLE_RE.finditer(pr["title"]):
        found |= fork_refs(m.group(1))
    return found & outside_issues


def build_threads(cache: dict) -> list[dict]:
    """Every thread where we owed someone a fix, as a time-ordered event list.

    Event kinds: deliver (we said it is done), ours (our other comments),
    comment (someone else's), reaction (the person's, on our comment),
    reopen (the person reopened it), pushback (the still-broken label),
    person_closed (the person closed it themselves).
    """
    issues = {i["number"]: i for i in cache["issues"]}
    outside_issues = {n for n, i in issues.items() if is_outside(login(i))}
    threads: dict[str, dict] = {}

    def thread(key, item, kind):
        if key not in threads:
            threads[key] = {"key": key, "kind": kind, "url": item["url"], "title": item["title"],
                            "person": login(item), "body": (item.get("body") or "")[:1500], "events": []}
        return threads[key]

    def add_comments(t, item, since=None):
        for c in item["comments"]["nodes"]:
            who = login(c)
            if since and c["createdAt"] <= since:
                continue
            if is_outside(who):
                t["events"].append({"at": c["createdAt"], "kind": "comment", "who": who, "url": c["url"],
                                    "body": c["body"] or ""})
            elif who == SELF:
                t["events"].append({"at": c["createdAt"], "kind": "ours", "who": who, "url": c["url"],
                                    "body": c["body"] or ""})
                for r in c["reactions"]["nodes"]:
                    # Reactions carry no time; they count for the comment they sit on.
                    if login(r) == t["person"] and r["content"] in POSITIVE_REACTIONS | NEGATIVE_REACTIONS:
                        t["events"].append({"at": c["createdAt"], "kind": "reaction", "who": login(r),
                                            "url": c["url"], "content": r["content"]})

    for n in outside_issues:
        i = issues[n]
        t = thread(f"issue#{n}", i, "issue")
        add_comments(t, i)
        outside_comment_times = sorted(c["createdAt"] for c in i["comments"]["nodes"] if is_outside(login(c)))
        tl = i["timelineItems"]["nodes"]
        for idx, e in enumerate(tl):
            kind = e["__typename"]
            if kind == "ClosedEvent":
                if e.get("stateReason") not in (None, "COMPLETED"):
                    continue  # not planned or duplicate: no claim that it is fixed
                if login(e) == t["person"]:
                    t["events"].append({"at": e["createdAt"], "kind": "person_closed", "who": login(e)})
                    continue
                nxt = next((x for x in tl[idx + 1:] if x["__typename"] in ("ReopenedEvent", "ClosedEvent")), None)
                if nxt and nxt["__typename"] == "ReopenedEvent" and login(nxt) == SELF and not any(
                        e["createdAt"] < c < nxt["createdAt"] for c in outside_comment_times):
                    continue  # we took the close back before anyone answered
                t["events"].append({"at": e["createdAt"], "kind": "deliver", "via": "closed"})
            elif kind == "ReopenedEvent" and is_outside(login(e)):
                prev = next((x for x in reversed(tl[:idx]) if x["__typename"] == "ClosedEvent"), None)
                if prev and login(prev) == t["person"] and login(e) == t["person"]:
                    # They reopened their own close: it no longer says they are happy,
                    # and it is not a fix of ours failing. Their words, if any, are judged.
                    for k in range(len(t["events"]) - 1, -1, -1):
                        if t["events"][k]["kind"] == "person_closed":
                            del t["events"][k]
                            break
                    continue
                t["events"].append({"at": e["createdAt"], "kind": "reopen", "who": login(e)})
            elif kind == "LabeledEvent" and e["label"]["name"] == "pushback":
                t["events"].append({"at": e["createdAt"], "kind": "pushback"})

    for pr in cache["prs"]:
        author = login(pr)
        if pr["mergedAt"]:
            for n in pr_targets(pr, outside_issues):
                if login(issues[n]) == author:
                    continue  # their own PR is its own thread below
                t = threads[f"issue#{n}"]
                t["events"].append({"at": pr["mergedAt"], "kind": "deliver", "via": f"PR #{pr['number']}"})
                add_comments(t, pr, since=pr["mergedAt"])  # people sometimes answer on the PR
        if is_outside(author):
            t = thread(f"pr#{pr['number']}", pr, "pr")
            seen = set()
            for e in pr["timelineItems"]["nodes"]:
                if e["__typename"] in ("MergedEvent", "ClosedEvent") and e["createdAt"] not in seen:
                    seen.add(e["createdAt"])  # a merge also emits a ClosedEvent at the same moment
                    merged = e["__typename"] == "MergedEvent" or e["createdAt"] == pr["mergedAt"]
                    t["events"].append({"at": e["createdAt"], "kind": "deliver", "via": "merged" if merged else "closed"})
                elif e["__typename"] == "ReopenedEvent" and is_outside(login(e)):
                    t["events"].append({"at": e["createdAt"], "kind": "reopen", "who": login(e)})
            first = min((e["at"] for e in t["events"] if e["kind"] == "deliver"), default=None)
            if first:
                add_comments(t, pr, since=first)

    for u in cache.get("upstream", []):
        t = thread(f"{u['repo']}#{u['number']}", u, "upstream")
        for c in u["comments"]["nodes"]:
            if login(c) == SELF and FORK_LINK_RE.search(c["body"] or ""):
                t["events"].append({"at": c["createdAt"], "kind": "deliver", "via": "our comment",
                                    "url": c["url"], "body": c["body"]})
        add_comments(t, u)
        # Our delivering comment is already the delivery; drop its duplicate "ours" copy.
        delivered = {e["url"] for e in t["events"] if e["kind"] == "deliver"}
        t["events"] = [e for e in t["events"] if not (e["kind"] == "ours" and e["url"] in delivered)]

    order = {"deliver": 0, "ours": 1, "reaction": 2, "pushback": 3, "reopen": 4, "person_closed": 5, "comment": 6}
    for t in threads.values():
        t["events"].sort(key=lambda e: (e["at"], order[e["kind"]]))
    return [t for t in threads.values() if any(e["kind"] == "deliver" for e in t["events"])]


def segment(thread: dict) -> list[dict]:
    """Split a thread into fix attempts. A delivery opens a new attempt once
    the current one has heard back; until then it joins the current one."""
    attempts: list[dict] = []
    cur = None
    for e in thread["events"]:
        if e["kind"] == "deliver":
            if cur is None or cur["heard"]:
                cur = {"thread": thread["key"], "start": e["at"], "via": [], "events": [], "heard": False}
                attempts.append(cur)
            cur["via"].append(e["via"])
            continue
        if cur is None:
            continue  # discussion before anything was delivered
        cur["events"].append(e)
        if e["kind"] in ("comment", "reopen", "pushback", "reaction", "person_closed"):
            cur["heard"] = True
    for a in attempts:
        a["id"] = f"{a['thread']}@{a['start']}"
        texts = [f"{e['who']}|{e['body']}" for e in a["events"] if e["kind"] == "comment"]
        a["hash"] = hashlib.sha256("\n".join(texts).encode()).hexdigest()[:16] if texts else None
    return attempts


def outcome(a: dict, verdicts: dict) -> tuple[str, str]:
    """(happy | not_fixed | no_reply | unread, basis)."""
    kinds = {e["kind"] for e in a["events"]}
    if "reopen" in kinds:
        return "not_fixed", "reopened by the person"
    if a["hash"]:
        # A judged reply outranks our pushback label: the label marks that they
        # pushed back, but their last word (a false alarm, a retest) decides.
        v = verdicts.get(a["id"])
        if not v or v.get("hash") != a["hash"]:
            return "unread", "reply not judged yet"
        if v["verdict"] in HAPPY_VERDICTS:
            return "happy", f"reply: {v['verdict']}"
        if v["verdict"] in BAD_VERDICTS:
            return "not_fixed", f"reply: {v['verdict']}"
    if "pushback" in kinds:
        return "not_fixed", "pushback label"
    reacts = [e["content"] for e in a["events"] if e["kind"] == "reaction"]
    if any(r in NEGATIVE_REACTIONS for r in reacts):
        return "not_fixed", "reaction"
    if any(r in POSITIVE_REACTIONS for r in reacts):
        return "happy", "reaction"
    if "person_closed" in kinds:
        return "happy", "closed by the person"
    return "no_reply", "nothing heard"


def resolved_attempts(cache: dict, verdicts: dict) -> list[dict]:
    """Attempts with outcomes. A quiet attempt folds into the next one, or,
    when it is the last, into a happy attempt before it (a quiet follow-up
    after the person said it works does not reopen the question)."""
    out = []
    for t in build_threads(cache):
        atts = segment(t)
        for a in atts:
            a["outcome"], a["basis"] = outcome(a, verdicts)
            a["first"] = a["start"]
        kept: list[dict] = []
        for idx, a in enumerate(atts):
            if a["outcome"] == "no_reply" and idx + 1 < len(atts):
                atts[idx + 1]["first"] = a["first"]
                atts[idx + 1]["via"] = a["via"] + atts[idx + 1]["via"]
            elif a["outcome"] == "no_reply" and kept and kept[-1]["outcome"] == "happy":
                kept[-1]["via"] += a["via"]
            else:
                kept.append(a)
        for a in kept:
            a.update(kind=t["kind"], url=t["url"], title=t["title"], person=t["person"], date=a["first"][:10])
        out += kept
    return sorted(out, key=lambda a: (a["date"], a["id"]))


# ---------------------------------------------------------- queue/apply ----

def load_cache() -> dict:
    if not CACHE.exists():
        sys.exit(f"no cache at {CACHE.relative_to(ROOT)}; run `{Path(__file__).name} fetch` first")
    return json.loads(CACHE.read_text())


def load_verdicts() -> dict:
    return json.loads(VERDICTS.read_text()) if VERDICTS.exists() else {}


def cmd_queue(args) -> None:
    cache, verdicts = load_cache(), load_verdicts()
    n = 0
    with open(args.out, "w") as fh:
        for t in build_threads(cache):
            for a in segment(t):
                if not a["hash"]:
                    continue
                v = verdicts.get(a["id"])
                if v and v.get("hash") == a["hash"] and not args.all:
                    continue
                said = [e for e in t["events"] if e["kind"] in ("ours", "deliver") and e.get("body")
                        and e["at"] <= a["start"]][-1:]
                fh.write(json.dumps({
                    "id": a["id"], "hash": a["hash"], "thread": t["url"], "title": t["title"],
                    "person": t["person"], "problem": t["body"][:800],
                    "delivered": {"at": a["start"], "via": a["via"],
                                  "our_last_word": said[0]["body"][:600] if said else ""},
                    "after": [{"who": e["who"],
                               "role": "us" if e["kind"] == "ours" else ("person" if e["who"] == t["person"] else "other"),
                               "at": e["at"], "url": e["url"], "text": e["body"][:1200]}
                              for e in a["events"] if e["kind"] in ("comment", "ours")],
                }, ensure_ascii=False) + "\n")
                n += 1
    print(f"{n} reply window(s) need a verdict -> {args.out}")


def cmd_apply(args) -> None:
    verdicts = load_verdicts()
    added = 0
    for line in Path(args.file).read_text().splitlines():
        if not line.strip():
            continue
        v = json.loads(line)
        if v.get("verdict") not in VERDICTS_ALLOWED or not v.get("id") or not v.get("hash"):
            sys.exit(f"bad verdict line; each needs id, hash and a verdict in {sorted(VERDICTS_ALLOWED)}: {line[:200]}")
        verdicts[v["id"]] = {"hash": v["hash"], "verdict": v["verdict"], "quote": (v.get("quote") or "")[:300],
                             "evidence": v.get("evidence_url") or "", "by": v.get("by") or args.by}
        added += 1
    DATA.mkdir(parents=True, exist_ok=True)
    VERDICTS.write_text(json.dumps(dict(sorted(verdicts.items())), indent=1, ensure_ascii=False) + "\n")
    print(f"applied {added} verdict(s); {len(verdicts)} stored in {VERDICTS.relative_to(ROOT)}")


# ------------------------------------------------------------ throughput ----

def pr_open_spans(pr: dict, now: dt.datetime) -> list[tuple[dt.datetime, dt.datetime]]:
    spans, opened = [], ts(pr["createdAt"])
    for e in sorted(pr["timelineItems"]["nodes"], key=lambda e: e["createdAt"]):
        if e["__typename"] in ("ClosedEvent", "MergedEvent") and opened:
            spans.append((opened, ts(e["createdAt"])))
            opened = None
        elif e["__typename"] == "ReopenedEvent" and not opened:
            opened = ts(e["createdAt"])
    if opened:
        spans.append((opened, now))
    return spans


def throughput(cache: dict) -> list[dict]:
    """Per UTC day: time-weighted open PRs, PRs opened so far, PRs closed by the day's end.
    The last row also carries open_now, the count open when the cache was fetched."""
    now = ts(cache["fetched_at"])
    spans = [pr_open_spans(p, now) for p in cache["prs"]]
    created = sorted(ts(p["createdAt"]) for p in cache["prs"])
    day = created[0].replace(hour=0, minute=0, second=0)
    rows = []
    while day < now:
        end = min(day + dt.timedelta(days=1), now)
        width = (end - day).total_seconds()
        open_avg = sum(max(0.0, (min(b, end) - max(a, day)).total_seconds()) for s in spans for a, b in s) / width
        made = sum(1 for c in created if c < end)
        still_open = sum(1 for s in spans if any(a < end < b or (a < end and b == now) for a, b in s))
        rows.append({"day": day.date(), "open_avg": open_avg, "opened": made, "closed": made - still_open})
        day += dt.timedelta(days=1)
    rows[-1]["open_now"] = sum(1 for s in spans if s and s[-1][1] == now)
    return rows


# ----------------------------------------------------------------- charts ----

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "secondary": "#52514e", "muted": "#898781",
              "grid": "#e1e0d9", "axis": "#c3c2b7", "series": "#2a78d6", "bad": "#d03b3b", "none": "#898781"},
    "dark": {"surface": "#1a1a19", "ink": "#f0efec", "secondary": "#c3c2b7", "muted": "#898781",
             "grid": "#2c2c2a", "axis": "#383835", "series": "#3987e5", "bad": "#e34948", "none": "#898781"},
}
# Theme keys, not status green: green and red sit 4 Delta E apart for deuteranopes; blue and red sit 24.
OUTCOMES = [("happy", "Happy", "series"), ("not_fixed", "Not fixed", "bad"), ("no_reply", "No reply", "none")]


def nice_scale(v: float, ticks: int = 4) -> tuple[float, float]:
    raw = max(v, 1) / ticks
    mag = 10 ** (len(str(int(raw))) - 1)
    step = next(s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw)
    return step * max(1, -(-v // step)), step


def fmt(n: float) -> str:
    return f"{n:,.0f}"


class Chart:
    """A small static SVG line chart: stacked panels sharing one x axis."""

    def __init__(self, theme: str, x0: dt.date, x1: dt.date, title: str, desc: str, width=880):
        self.c, self.x0, self.x1, self.w = THEMES[theme], x0, x1, width
        self.left, self.right, self.parts, self.y = 52, 132, [], 0
        self.title, self.desc = title, desc

    def x(self, d: dt.date) -> float:
        span = max((self.x1 - self.x0).days, 1)
        return self.left + (self.w - self.left - self.right) * (d - self.x0).days / span

    def text(self, x, y, s, color, size=12, anchor="start", weight=400):
        self.parts.append(f'<text x="{x:.1f}" y="{y:.1f}" fill="{color}" font-size="{size}" '
                          f'font-weight="{weight}" text-anchor="{anchor}">{escape(s)}</text>')

    def panel(self, heading: str, series: list[dict], height=150, legend=False):
        c = self.c
        self.text(self.left, self.y + 20, heading, c["ink"], 13, weight=600)
        if legend:
            lx, ly = self.left, self.y + 40
            for s in series:
                self.parts.append(f'<line x1="{lx}" y1="{ly - 4}" x2="{lx + 16}" y2="{ly - 4}" '
                                  f'stroke="{s["color"]}" stroke-width="2" stroke-linecap="round"/>')
                self.text(lx + 22, ly, s["name"], c["secondary"])
                lx += 22 + 7 * len(s["name"]) + 20
            self.y += 20
        top = self.y + 36
        ymax, step = nice_scale(max((v for s in series for _, v in s["points"]), default=1))
        y = lambda v: top + height - height * v / ymax  # noqa: E731
        k = 0.0
        while k <= ymax + 1e-9:
            self.parts.append(f'<line x1="{self.left}" y1="{y(k):.1f}" x2="{self.w - self.right}" y2="{y(k):.1f}" '
                              f'stroke="{c["axis"] if k == 0 else c["grid"]}" stroke-width="1"/>')
            self.text(self.left - 8, y(k) + 4, fmt(k), c["muted"], 11, "end")
            k += step
        ends = []
        for s in series:
            pts, cut = s["points"], s.get("settling_from")
            solid = [p for p in pts if not cut or p[0] <= cut]
            dashed = [p for p in pts if cut and p[0] >= cut]
            for seg, dash in ((solid, ""), (dashed, ' stroke-dasharray="4 4"')):
                if len(seg) > 1:
                    d = " ".join(f"{'M' if i == 0 else 'L'}{self.x(p[0]):.1f},{y(p[1]):.1f}" for i, p in enumerate(seg))
                    self.parts.append(f'<path d="{d}" fill="none" stroke="{s["color"]}" stroke-width="2" '
                                      f'stroke-linejoin="round" stroke-linecap="round"{dash}/>')
            last = pts[-1]
            self.parts.append(f'<circle cx="{self.x(last[0]):.1f}" cy="{y(last[1]):.1f}" r="4" fill="{s["color"]}" '
                              f'stroke="{c["surface"]}" stroke-width="2"/>')
            ends.append([y(last[1]) + 4, s["end_label"]])
        ends.sort()
        for i in range(1, len(ends)):
            ends[i][0] = max(ends[i][0], ends[i - 1][0] + 15)
        for ly, label in ends:
            self.text(self.w - self.right + 12, ly, label, c["ink"])
        self.y = top + height + 4

    def x_axis(self):
        d = self.x0.replace(day=1)
        while d <= self.x1:
            if d >= self.x0:
                self.text(self.x(d), self.y + 14, d.strftime("%b %Y" if d.month == 1 else "%b"),
                          self.c["muted"], 11, "middle")
            d = (d + dt.timedelta(days=32)).replace(day=1)
        self.y += 22

    def note(self, s: str):
        self.text(self.left, self.y + 14, s, self.c["muted"], 11)
        self.y += 20

    def svg(self) -> str:
        h = self.y + 10
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{h:.0f}" '
                f'viewBox="0 0 {self.w} {h:.0f}" role="img" aria-labelledby="t d" font-family="{FONT}">'
                f'<title id="t">{escape(self.title)}</title><desc id="d">{escape(self.desc)}</desc>'
                f'<rect width="100%" height="100%" rx="6" fill="{self.c["surface"]}"/>'
                + "".join(self.parts) + "</svg>\n")


def weekly(attempts: list[dict], first: dt.date, last: dt.date) -> list[tuple[dt.date, Counter]]:
    start = first - dt.timedelta(days=first.weekday())
    buckets = []
    while start <= last:
        buckets.append((start, Counter()))
        start += dt.timedelta(days=7)
    for a in attempts:
        d = dt.date.fromisoformat(a["date"])
        buckets[(d - buckets[0][0]).days // 7][1][a["outcome"]] += 1
    return buckets


def render_charts(rows: list[dict], attempts: list[dict], today: dt.date) -> dict[str, str]:
    x0, x1, now = rows[0]["day"], rows[-1]["day"], rows[-1]
    weeks = weekly(attempts, x0, x1)
    settle = today - SETTLING
    settle_mid = settle - dt.timedelta(days=settle.weekday()) + dt.timedelta(days=3)
    totals = Counter(a["outcome"] for a in attempts)
    out = {}
    for theme, c in THEMES.items():
        ch = Chart(theme, x0, x1, "Pull request throughput",
                   f"{now['open_now']} pull requests open on {today:%b} {today.day}; "
                   f"{fmt(now['closed'])} closed since {x0:%B %Y}.")
        ch.panel("Open pull requests, daily average",
                 [{"name": "open", "color": c["series"], "points": [(r["day"], r["open_avg"]) for r in rows],
                   "end_label": f"{now['open_now']} open"}], height=110)
        ch.panel("Pull requests closed, running total",
                 [{"name": "closed", "color": c["series"], "points": [(r["day"], r["closed"]) for r in rows],
                   "end_label": f"{fmt(now['closed'])} closed"}], height=140)
        ch.x_axis()
        out[f"progress-throughput-{theme}.svg"] = ch.svg()

        ch = Chart(theme, x0, x1, "Fix outcomes by week",
                   f"Of {fmt(len(attempts))} fixes delivered to someone, {fmt(totals['happy'])} heard back happy, "
                   f"{fmt(totals['not_fixed'])} were not fixed and {fmt(totals['no_reply'])} got no reply.")
        series = [{"name": name, "color": c[color], "settling_from": settle_mid,
                   "points": [(min(wk + dt.timedelta(days=3), x1), cnt[key]) for wk, cnt in weeks],
                   "end_label": f"{fmt(totals[key])} {name.lower()} in all"} for key, name, color in OUTCOMES]
        ch.panel("Fixes delivered each week, by what the person said next", series, height=180, legend=True)
        ch.x_axis()
        ch.note("Dashed weeks are under two weeks old, so their replies are still coming in.")
        out[f"progress-outcomes-{theme}.svg"] = ch.svg()
    return out


# ----------------------------------------------------------------- render ----

def picture(stem: str, alt: str) -> str:
    return (f'<picture>\n  <source media="(prefers-color-scheme: dark)" srcset="README_images/{stem}-dark.svg">\n'
            f'  <img alt="{escape(alt)}" src="README_images/{stem}-light.svg" width="100%">\n</picture>')


def monthly_table(rows: list[dict], attempts: list[dict]) -> list[str]:
    months: dict[str, dict] = {}
    for r in rows:
        m = months.setdefault(r["day"].strftime("%Y-%m"), {"open": [], **{k: 0 for k, _, _ in OUTCOMES}})
        m["open"].append(r["open_avg"])
        m["last"] = r
    for a in attempts:
        months[a["date"][:7]][a["outcome"]] += 1
    table = ["| Month | PRs opened | PRs closed | Open, daily average | Happy | Not fixed | No reply |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    prev = {"opened": 0, "closed": 0}
    for key in sorted(months):
        m = months[key]
        table.append(f"| {dt.date.fromisoformat(key + '-01'):%b %Y} | {fmt(m['last']['opened'] - prev['opened'])} | "
                     f"{fmt(m['last']['closed'] - prev['closed'])} | {sum(m['open']) / len(m['open']):.0f} | "
                     f"{m['happy']} | {m['not_fixed']} | {m['no_reply']} |")
        prev = m["last"]
    return table


def readme_block(rows: list[dict], attempts: list[dict], cache: dict, today: dt.date, unread: int) -> str:
    merged = sum(1 for p in cache["prs"] if p["mergedAt"])
    now = rows[-1]
    totals = Counter(a["outcome"] for a in attempts)
    heard = totals["happy"] + totals["not_fixed"]
    share = lambda k: f"{100 * totals[k] / max(len(attempts), 1):.0f}%"  # noqa: E731
    return "\n".join([
        picture("progress-throughput", f"Pull request throughput: {now['open_now']} open, "
                f"{fmt(now['closed'])} closed since {rows[0]['day']:%B %Y}."),
        "",
        picture("progress-outcomes", f"Fix outcomes by week: {totals['happy']} happy, "
                f"{totals['not_fixed']} not fixed, {totals['no_reply']} no reply."),
        "",
        f"On {today:%B} {today.day}, {today.year}, {now['open_now']} pull requests were open and "
        f"{fmt(now['closed'])} closed, {fmt(merged)} of them merged. Of {fmt(len(attempts))} fixes delivered "
        f"to a person, {share('happy')} heard back happy, {share('not_fixed')} not fixed and "
        f"{share('no_reply')} got no reply. When someone did answer, the answer was happy "
        f"{100 * totals['happy'] / max(heard, 1):.0f}% of the time."
        + (f" {unread} replies wait to be judged and are left out." if unread else ""),
        "",
        "A fix is one moment someone was told their problem was dealt with: a fix PR merged for their issue, "
        "their issue closed as done, their own PR merged or closed, or our note on an upstream CWA or "
        "Calibre-Web thread pointing them to a fix here. Happy means they then said it works or were content "
        "with it. Not fixed means they reopened it, said it is still broken in whole or part, or were unhappy "
        "with how it went, and every close in a close-and-reopen cycle counts. No reply means nothing came "
        "back. Each reply is judged against [a written rubric](docs/progress-tracker/JUDGING.md) by one model "
        "and checked by a second; where they disagree the thread is reread and settled. Every verdict and the "
        "quote behind it are in [docs/progress-tracker](docs/progress-tracker/). "
        "Replies on Discord and Reddit are not counted.",
        "",
        "<details><summary>The numbers by month</summary>",
        "",
        *monthly_table(rows, attempts),
        "",
        "Regenerate with `python3 scripts/progress_tracker.py fetch`, then `queue`, judge the new replies, "
        "`apply` and `render`.",
        "",
        "</details>",
    ])


def cmd_render(args) -> None:
    cache, verdicts = load_cache(), load_verdicts()
    today = ts(cache["fetched_at"]).date()
    rows = throughput(cache)
    attempts = resolved_attempts(cache, verdicts)
    counted = [a for a in attempts if a["outcome"] != "unread"]
    for name, svg in render_charts(rows, counted, today).items():
        (IMAGES / name).write_text(svg)
    DATA.mkdir(parents=True, exist_ok=True)
    ledger = []
    for a in attempts:
        v = verdicts.get(a["id"], {}) if a["basis"].startswith("reply") else {}
        ledger.append({"date": a["date"], "thread": a["url"], "title": a["title"], "person": a["person"],
                       "delivered_via": a["via"], "outcome": a["outcome"], "basis": a["basis"],
                       "quote": v.get("quote", ""), "evidence": v.get("evidence", ""),
                       "judged_by": v.get("by", "") if v else "rule", "id": a["id"]})
    ATTEMPTS.write_text(json.dumps(ledger, indent=1, ensure_ascii=False) + "\n")
    text = README.read_text()
    s, e = text.find(START), text.find(END)
    if s < 0 or e < s:
        sys.exit(f"README.md has no {START} ... {END} block")
    head = text[:text.index("\n", s) + 1]
    block = readme_block(rows, counted, cache, today, len(attempts) - len(counted))
    README.write_text(head + block + "\n" + text[e:])
    print(f"rendered {len(rows)} days and {len(attempts)} fix attempts "
          f"{dict(Counter(a['outcome'] for a in attempts))}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch").set_defaults(fn=cmd_fetch)
    q = sub.add_parser("queue")
    q.add_argument("--out", default="progress-queue.jsonl")
    q.add_argument("--all", action="store_true", help="include windows that already have a verdict")
    q.set_defaults(fn=cmd_queue)
    a = sub.add_parser("apply")
    a.add_argument("file")
    a.add_argument("--by", default="", help="who judged these; recorded with each verdict")
    a.set_defaults(fn=cmd_apply)
    sub.add_parser("render").set_defaults(fn=cmd_render)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
