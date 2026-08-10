#!/usr/bin/env python3
"""Refuse to publish a doc whose checkable claims are false.

A user-facing page that is subtly wrong is worse than no page, because once it
is cited it becomes the reference. Most of what goes wrong in a drafted doc is
not subtle, though — it is a route that 404s, a version that was never tagged,
a container name nobody ships, a file path that does not exist. Those are all
decidable against the repo, so they should never reach a reviewer's judgement.

This is the mechanical half of the anchoring rule. It cannot judge whether the
prose is *true* — that still needs an adversarial reader — but it makes the
class of error that reads most authoritatively impossible to publish.

Every check here earned its place by catching a real defect in the first
Kobo-shelves draft:

  --route      `/admin/cwa_settings` was a 404; the route is `/cwa-settings`.
               It was the article's central instruction.
  --container  `docker logs -f calibre-web` names a container the shipped
               compose does not create (`calibre-web-nextgen`).
  --version    a fix attributed to the wrong release is unfalsifiable to a
               reader and sends them to an image that does not contain it.
  --path       a cited file that has moved leaves the claim unanchorable.

Usage:
    scripts/verify-doc-claims.py docs/kobo-shelves.md [more.md ...]
    scripts/verify-doc-claims.py --repo /path/to/repo docs/*.md

Exit 0 = every checkable claim resolves. Exit 1 = at least one is false.
"""

import argparse
import os
import re
import subprocess
import sys

# A route claim: `/foo` or `/foo-bar` inside backticks. Deliberately narrow —
# only backticked, only leading slash, only path-shaped — because prose is full
# of slashes and a greedy pattern produces noise nobody reads.
ROUTE_RE = re.compile(r"`(/[a-z0-9][a-z0-9_/-]*)`")
VERSION_RE = re.compile(r"\bv(\d+\.\d+\.\d+)\b")
# A path claim: something.ext or dir/something.ext in backticks.
PATH_RE = re.compile(r"`([A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|tsx|ts|js|html|md|yml|yaml|sh|toml))`")
CONTAINER_RE = re.compile(r"docker\s+(?:logs|exec|restart|stop)\s+(?:-\w+\s+)*([A-Za-z0-9][A-Za-z0-9_.-]*)")

# Route prefixes served by something other than a Flask decorator, or claimed
# in a context where a literal match is wrong. Extend with a REASON, never to
# silence a genuine miss.
ROUTE_ALLOW = {
    "/kobo/",          # per-token Kobo endpoints, built at runtime
    "/config",         # a directory path in Docker docs, not a route
    "/opds",           # blueprint root, registered without a bare rule
}


def sh(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def known_routes(repo):
    """Every Flask route literal declared in cps/."""
    out = sh(["grep", "-rhoE", r"""@[a-zA-Z_]+\.route\(["'][^"']+["']""", "cps"], repo)
    routes = set()
    for line in out.stdout.splitlines():
        m = re.search(r"""["']([^"']+)["']""", line)
        if m:
            routes.add(m.group(1))
    return routes


def spa_routes(repo):
    """Every path the new UI serves, from frontend/src/lib/routes.ts.

    Returns None when the file cannot be read, which suppresses the
    classic-only note rather than emitting a wrong one.
    """
    p = os.path.join(repo, "frontend", "src", "lib", "routes.ts")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return set(re.findall(r"""['"](/[^'"]*)['"]""", fh.read()))


def released_versions(repo):
    out = sh(["git", "tag", "--list", "v*"], repo)
    return {t.strip().lstrip("v") for t in out.stdout.splitlines() if t.strip()}


def compose_containers(repo):
    names = set()
    for fn in ("docker-compose.yml", "docker-compose.yaml", "compose.yml"):
        p = os.path.join(repo, fn)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    m = re.search(r"container_name:\s*(\S+)", line)
                    if m:
                        names.add(m.group(1))
    return names


def check(doc, repo, routes, versions, containers, spa=None):
    """Return (failures, notes) — notes never fail the run."""
    with open(doc, encoding="utf-8") as fh:
        text = fh.read()
    bad = []
    notes = []

    for claim in sorted(set(ROUTE_RE.findall(text))):
        if claim in ROUTE_ALLOW or any(claim.startswith(a) for a in ROUTE_ALLOW):
            continue
        # A route matches if it is declared exactly, or is the static prefix of
        # a parameterised rule (/book/<id> covers a claimed /book).
        if claim in routes or any(r.startswith(claim.rstrip("/") + "/<") for r in routes):
            # The route is real. Is it reachable from the NEW UI, though?
            # A page that says "go to /x" without saying /x is classic-only
            # sends an SPA user hunting for something that isn't there — the
            # parity failure mode, and indistinguishable from a wrong route
            # to the person reading. Advisory, not a failure: documenting a
            # classic-only path is often exactly the right thing to do, as
            # long as the page says so.
            if spa is not None and claim not in spa and "classic" not in text.lower():
                notes.append(("classic-only", claim,
                              "served by cps/ but absent from SPA_ROUTES, and this page "
                              "never says 'classic' — an SPA user will not find it"))
            continue
        near = sorted(r for r in routes if claim.strip("/").split("/")[0] in r)[:3]
        bad.append(("route", claim,
                    f"no Flask route declares it" + (f"; nearest: {', '.join(near)}" if near else "")))

    for claim in sorted(set(VERSION_RE.findall(text))):
        if claim not in versions:
            bad.append(("version", f"v{claim}", "no such release tag"))

    for claim in sorted(set(PATH_RE.findall(text))):
        if not os.path.exists(os.path.join(repo, claim)):
            bad.append(("path", claim, "file does not exist in the repo"))

    if containers:
        for claim in sorted(set(CONTAINER_RE.findall(text))):
            if claim.startswith("-"):
                continue
            if claim not in containers:
                bad.append(("container", claim,
                            f"compose creates {', '.join(sorted(containers))}"))
    return bad, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("docs", nargs="+")
    ap.add_argument("--repo", default=None)
    args = ap.parse_args()

    repo = args.repo or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    routes, versions, containers = known_routes(repo), released_versions(repo), compose_containers(repo)
    spa = spa_routes(repo)
    if not routes:
        print("verify-doc-claims: could not read any Flask routes — refusing to "
              "report a clean pass on an empty corpus", file=sys.stderr)
        return 2

    failures = 0
    for doc in args.docs:
        bad, notes = check(doc, repo, routes, versions, containers, spa)
        for kind, claim, detail in notes:
            print(f"{doc}: NOTE [{kind}] {claim} — {detail}")
        if bad:
            failures += len(bad)
            print(f"{doc}: {len(bad)} unanchored claim(s)")
            for kind, claim, detail in bad:
                print(f"  [{kind}] {claim} — {detail}")
        else:
            print(f"{doc}: OK")

    if failures:
        print(f"\n{failures} claim(s) could not be anchored. A published page is cited as "
              f"the reference; fix or remove them.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
