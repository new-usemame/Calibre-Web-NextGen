#!/usr/bin/env python3
"""Internal findings ledger — the autopilot's own audit output, kept out of the issue tracker.

Why this exists
---------------
The issue tracker is for people. When a maintainer pass audits a subsystem it turns up
real defects at a far higher rate than any one pass can fix them, and filing each one as a
GitHub issue buries the handful of threads where a user is actually waiting. Those findings
still matter, so they live here: version-controlled, greppable, and linkable, but out of the
way.

A finding is promoted to a real issue only when a user reports it independently
(`findings.py promote`), at which point the thread belongs to that user.

Storage is one JSON file per finding under findings/items/, which makes concurrent branches
merge without conflicts. IDs are derived from content, so the same finding filed twice gets
the same id and the second write is a no-op.

Stdlib only, by project rule (no new dependencies).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ITEMS = ROOT / "findings" / "items"
INDEX = ROOT / "findings" / "INDEX.md"

# Ordered most-urgent first; the index and `list` default to this order.
SEVERITIES = [
    "security",        # exploitable, or exposes data/credentials
    "data-integrity",  # loses, corrupts, or silently discards user data
    "correctness",     # wrong behaviour a user could hit
    "ux",              # confusing or missing affordance
    "perf",            # slow or wasteful, not wrong
    "test",            # gap in coverage or a broken/misleading test
    "docs",            # documentation or comment defect
    "chore",           # internal tidiness, no user-visible effect
]

STATUSES = ["open", "resolved", "wontfix", "duplicate"]

SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be", "been",
    "to", "of", "in", "on", "for", "with", "at", "by", "from", "as", "it", "its",
    "this", "that", "these", "those", "not", "no", "can", "does", "do", "did",
    "when", "while", "if", "then", "than", "so", "still", "even", "only", "also",
}


# ---------------------------------------------------------------- helpers


def _slug_tokens(text: str) -> set[str]:
    """Content words of `text`, for the cheap duplicate check."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def _make_id(title: str, area: str) -> str:
    """Content-derived id, so re-filing the same finding is idempotent.

    Keyed on the normalised title plus area rather than the body: bodies get edited and
    reworded, and a finding that changes wording is still the same finding.
    """
    basis = f"{area}\x00{' '.join(sorted(_slug_tokens(title)))}"
    return "F-" + hashlib.sha256(basis.encode()).hexdigest()[:6]


def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temp file in the same dir, so a crash can't leave a truncated ledger."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".swap")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_all() -> list[dict]:
    if not ITEMS.is_dir():
        return []
    out = []
    for p in sorted(ITEMS.glob("F-*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            print(f"warning: {p.name} is not valid JSON ({exc}); skipping", file=sys.stderr)
    return out


def save(finding: dict) -> Path:
    path = ITEMS / f"{finding['id']}.json"
    _write_atomic(path, json.dumps(finding, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return path


def _sort_key(f: dict) -> tuple:
    return (SEV_RANK.get(f.get("severity", "chore"), 99), f.get("created", ""), f.get("id", ""))


# ---------------------------------------------------------------- commands


def cmd_add(args) -> int:
    area = args.area.strip().lower()
    title = args.title.strip()
    if not title:
        print("error: empty title", file=sys.stderr)
        return 2
    if args.severity not in SEVERITIES:
        print(f"error: severity must be one of {', '.join(SEVERITIES)}", file=sys.stderr)
        return 2

    fid = _make_id(title, area)
    path = ITEMS / f"{fid}.json"
    if path.exists() and not args.force:
        print(f"{fid} already exists (same title+area) — not re-filing. Use --force to overwrite.")
        return 0

    body = args.body
    if body == "-":
        body = sys.stdin.read()

    finding = {
        "id": fid,
        "title": title,
        "area": area,
        "severity": args.severity,
        "status": "open",
        "created": _today(),
        "body": (body or "").strip(),
        "refs": args.ref or [],
        "source": args.source,
    }
    if args.origin_issue:
        finding["origin_issue"] = args.origin_issue

    # Warn on a probable duplicate rather than blocking — the operator decides.
    dupes = _near_duplicates(finding, load_all())
    if dupes:
        print("note: similar existing findings:")
        for score, other in dupes[:3]:
            print(f"  {other['id']}  ({score:.0%} title overlap)  {other['title'][:70]}")

    save(finding)
    print(f"{fid}  [{finding['severity']}/{area}]  {title}")
    return 0


def _near_duplicates(finding: dict, corpus: list[dict]) -> list[tuple[float, dict]]:
    """Jaccard overlap on title tokens. Deliberately crude: a hint, not a gate."""
    mine = _slug_tokens(finding["title"])
    if not mine:
        return []
    hits = []
    for other in corpus:
        if other.get("id") == finding.get("id"):
            continue
        theirs = _slug_tokens(other.get("title", ""))
        if not theirs:
            continue
        overlap = len(mine & theirs) / len(mine | theirs)
        if overlap >= 0.5:
            hits.append((overlap, other))
    return sorted(hits, key=lambda t: -t[0])


def cmd_list(args) -> int:
    items = load_all()
    if not args.all:
        items = [f for f in items if f.get("status") == "open"]
    if args.severity:
        items = [f for f in items if f.get("severity") in args.severity]
    if args.area:
        items = [f for f in items if f.get("area") in args.area]
    if args.grep:
        pat = re.compile(args.grep, re.I)
        items = [f for f in items if pat.search(f.get("title", "")) or pat.search(f.get("body", ""))]

    items.sort(key=_sort_key)
    if args.json:
        print(json.dumps(items, indent=2, ensure_ascii=False))
        return 0
    for f in items:
        flag = "" if f.get("status") == "open" else f"  ({f['status']})"
        origin = f"  <-#{f['origin_issue']}" if f.get("origin_issue") else ""
        print(f"{f['id']}  {f.get('severity',''):<15} {f.get('area',''):<12} {f.get('title','')[:72]}{origin}{flag}")
    if not args.quiet:
        print(f"\n{len(items)} finding(s)")
    return 0


def cmd_show(args) -> int:
    for f in load_all():
        if f["id"] == args.id or f["id"] == f"F-{args.id}":
            print(json.dumps(f, indent=2, ensure_ascii=False) if args.json else _render(f))
            return 0
    print(f"error: no finding {args.id}", file=sys.stderr)
    return 1


def _render(f: dict) -> str:
    lines = [
        f"{f['id']}  [{f.get('severity')}/{f.get('area')}]  {f.get('status')}",
        f"{f.get('title')}",
        f"filed {f.get('created')}" + (f"  (migrated from #{f['origin_issue']})" if f.get("origin_issue") else ""),
    ]
    if f.get("refs"):
        lines.append("refs: " + ", ".join(f["refs"]))
    if f.get("resolution"):
        r = f["resolution"]
        lines.append(f"resolved {r.get('date','')}  {r.get('commit','')}  {r.get('release','')}  {r.get('note','')}")
    lines.append("")
    lines.append(f.get("body", ""))
    return "\n".join(lines)


def cmd_resolve(args) -> int:
    for f in load_all():
        if f["id"] == args.id:
            f["status"] = args.status
            f["resolution"] = {
                "date": _today(),
                "commit": args.commit or "",
                "release": args.release or "",
                "note": args.note or "",
            }
            save(f)
            print(f"{f['id']} -> {args.status}")
            return 0
    print(f"error: no finding {args.id}", file=sys.stderr)
    return 1


def cmd_promote(args) -> int:
    """Record that a finding became a real user-facing issue.

    Promotion is what happens when a user independently reports the thing. It does not
    create the issue (that is `gh issue create`, so the wording is a human decision) — it
    records the link and takes the finding off the open ledger.
    """
    for f in load_all():
        if f["id"] == args.id:
            f["status"] = "resolved"
            f["resolution"] = {
                "date": _today(),
                "commit": "",
                "release": "",
                "note": f"promoted to #{args.issue} (reported independently by a user)",
            }
            f.setdefault("refs", []).append(f"#{args.issue}")
            save(f)
            print(f"{f['id']} promoted -> #{args.issue}")
            return 0
    print(f"error: no finding {args.id}", file=sys.stderr)
    return 1


def cmd_dedupe(args) -> int:
    items = [f for f in load_all() if f.get("status") == "open"]
    seen: set[tuple[str, str]] = set()
    n = 0
    for f in items:
        for score, other in _near_duplicates(f, items):
            key = tuple(sorted((f["id"], other["id"])))
            if key in seen:
                continue
            seen.add(key)
            n += 1
            print(f"{score:.0%}  {f['id']} {f['title'][:58]}")
            print(f"      {other['id']} {other['title'][:58]}\n")
    print(f"{n} candidate pair(s)" if n else "no near-duplicates")
    return 0


def cmd_stats(args) -> int:
    items = load_all()
    openi = [f for f in items if f.get("status") == "open"]
    print(f"total {len(items)}   open {len(openi)}   closed {len(items)-len(openi)}\n")
    print("open by severity:")
    for sev in SEVERITIES:
        n = sum(1 for f in openi if f.get("severity") == sev)
        if n:
            print(f"  {sev:<15} {n}")
    areas: dict[str, int] = {}
    for f in openi:
        areas[f.get("area", "?")] = areas.get(f.get("area", "?"), 0) + 1
    print("\nopen by area:")
    for a, n in sorted(areas.items(), key=lambda kv: -kv[1]):
        print(f"  {a:<15} {n}")
    return 0


def cmd_index(args) -> int:
    items = load_all()
    openi = sorted([f for f in items if f.get("status") == "open"], key=_sort_key)
    done = sorted([f for f in items if f.get("status") != "open"], key=_sort_key)

    out = [
        "# Findings",
        "",
        "Internal audit findings from maintainer passes over the codebase. **This is not the",
        "bug tracker** — if you hit a problem, please open an issue instead:",
        "<https://github.com/new-usemame/Calibre-Web-NextGen/issues>.",
        "",
        "These are things a maintenance pass noticed in passing and recorded so they are not",
        "lost. They are unprioritised against user reports, and a user report always outranks",
        "anything here. Generated by `scripts/findings.py index` — edit the JSON, not this file.",
        "",
        f"{len(openi)} open, {len(done)} closed.",
        "",
    ]

    if openi:
        out += ["## Open", ""]
        current = None
        for f in openi:
            if f.get("severity") != current:
                current = f.get("severity")
                out += ["", f"### {current}", "", "| id | area | finding |", "|---|---|---|"]
            title = f.get("title", "").replace("|", "\\|")
            out.append(f"| `{f['id']}` | {f.get('area','')} | {title} |")
        out.append("")

    if done:
        out += ["", "## Closed", "", "| id | status | finding | resolved |", "|---|---|---|---|"]
        for f in done:
            title = f.get("title", "").replace("|", "\\|")
            r = f.get("resolution") or {}
            when = r.get("release") or r.get("commit", "")[:8] or r.get("date", "")
            out.append(f"| `{f['id']}` | {f.get('status')} | {title} | {when} |")
        out.append("")

    _write_atomic(INDEX, "\n".join(out))
    try:
        shown = INDEX.relative_to(ROOT)
    except ValueError:
        # INDEX can be pointed outside the repo (tests, or a one-off export); reporting the
        # path must never be the thing that fails the write that already succeeded.
        shown = INDEX
    print(f"wrote {shown}  ({len(openi)} open, {len(done)} closed)")
    return 0


# ---------------------------------------------------------------- cli


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="findings.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="record a new finding")
    a.add_argument("title")
    a.add_argument("--area", required=True, help="subsystem, e.g. kobo, ingest, spa, api, ci")
    a.add_argument("--severity", required=True, choices=SEVERITIES)
    a.add_argument("--body", default="", help="detail; '-' reads stdin")
    a.add_argument("--ref", action="append", help="related issue/PR, repeatable (e.g. '#324')")
    a.add_argument("--origin-issue", type=int, help="issue number this was migrated from")
    a.add_argument("--source", default="audit", choices=["audit", "migrated-issue"])
    a.add_argument("--force", action="store_true", help="overwrite an existing finding")
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("list", help="list findings (open by default)")
    l.add_argument("--all", action="store_true", help="include closed")
    l.add_argument("--severity", action="append", choices=SEVERITIES)
    l.add_argument("--area", action="append")
    l.add_argument("--grep", help="regex over title and body")
    l.add_argument("--json", action="store_true")
    l.add_argument("--quiet", action="store_true")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="print one finding")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_show)

    r = sub.add_parser("resolve", help="close a finding")
    r.add_argument("id")
    r.add_argument("--status", default="resolved", choices=["resolved", "wontfix", "duplicate"])
    r.add_argument("--commit", help="fixing commit sha")
    r.add_argument("--release", help="release tag that shipped the fix")
    r.add_argument("--note")
    r.set_defaults(func=cmd_resolve)

    pr = sub.add_parser("promote", help="record that a user reported this independently")
    pr.add_argument("id")
    pr.add_argument("issue", type=int, help="the GitHub issue number it became")
    pr.set_defaults(func=cmd_promote)

    d = sub.add_parser("dedupe", help="show probable duplicate pairs")
    d.set_defaults(func=cmd_dedupe)

    st = sub.add_parser("stats", help="counts by severity and area")
    st.set_defaults(func=cmd_stats)

    ix = sub.add_parser("index", help="regenerate findings/INDEX.md")
    ix.set_defaults(func=cmd_index)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
