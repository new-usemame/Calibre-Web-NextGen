# Findings

**This is not the bug tracker.** If something is broken for you, please open an issue:
<https://github.com/new-usemame/Calibre-Web-NextGen/issues>. That tracker is for people —
every thread there has someone waiting on it.

This directory is where maintenance passes record what they notice *in passing*. When a
pass fixes a bug in, say, the Kobo sync handler, it reads a lot of surrounding code, and it
routinely spots other things that are wrong. Those observations are worth keeping, but they
are not user reports and they should not compete with user reports for attention.

So they live here: version-controlled, greppable, and linkable — and out of the way.

## Layout

```
findings/
  items/F-xxxxxx.json   one file per finding (merge-safe; no shared file to conflict on)
  INDEX.md              generated human-readable view, sorted most-urgent first
  README.md             this file
```

Finding ids are derived from the title and area, so re-filing the same observation is a
no-op rather than a duplicate.

## Using it

```bash
scripts/findings.py list                      # open findings, most urgent first
scripts/findings.py list --severity security --severity data-integrity
scripts/findings.py list --area kobo
scripts/findings.py list --grep "reading position"
scripts/findings.py show F-5aec96
scripts/findings.py stats                     # counts by severity and area
scripts/findings.py dedupe                    # probable duplicate pairs

scripts/findings.py add "Title" --area kobo --severity correctness --body -
scripts/findings.py resolve F-5aec96 --release v4.1.31 --commit abc1234
scripts/findings.py promote F-5aec96 1391     # a user reported it independently
scripts/findings.py index                     # regenerate INDEX.md
```

Regenerate `INDEX.md` whenever items change; it is generated output, so edit the JSON.

## Severity

Ordered by how much it matters, not by how hard it is:

| | |
|---|---|
| `security` | exploitable, or exposes data or credentials |
| `data-integrity` | loses, corrupts, or silently discards user data |
| `correctness` | wrong behaviour a user could hit |
| `ux` | confusing or missing affordance |
| `perf` | slow or wasteful, not wrong |
| `test` | coverage gap, or a test that misleads |
| `docs` | documentation or comment defect |
| `chore` | internal tidiness, no user-visible effect |

## When a finding becomes an issue

A finding is promoted to a real GitHub issue when **a user reports it independently**. At
that point it stops being an internal observation and becomes a thread someone is waiting
on. Record the link with `findings.py promote <id> <issue>`.

Findings are not promoted merely because they are old or because someone wants them more
visible — that is what reintroduces the noise this directory exists to prevent.

Security findings are the exception to keeping things here: an exploitable defect in a
released build is handled through [SECURITY.md](../SECURITY.md), not filed and shelved.
