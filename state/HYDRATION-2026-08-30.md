# Post-compaction hydration (boundary 2, 2026-08-30T00:31Z) — CWNG NEW UI DEFAULT

Mined the full transcript (3612 records) for all six operator-instruction record shapes.
Verdict: **nothing materially lost**, two recoveries below.

## What the six shapes actually contained

| Shape | Found | Operator content |
|---|---|---|
| 1. `type:"user"` (all promptSource) | 65 with text | 4 operator-typed: the opening brief pointer, 2x `/compact`, "Continue from where you left off." The other 61 are peer relays, task notifications, and one `/security-review` skill body. |
| 2. `attachment` / `queued_command` | 17 | **1 human-origin** (`origin.kind=="human"`): *"This should all be done test driven and strive for 100% coverage"* (00:38:41Z). Other 16 are task notifications. |
| 3. `queue-operation.content` | 138 | Zero human-origin. All notification/relay echoes. |
| 4. `goal_status` / `.condition` | **0 real records** | The 4 grep hits are the dispatcher's own hydrate-order text naming the shape. No `/goal` was ever run this session (the brief's standing goal was never latched as a goal record). |
| 5. `AskUserQuestion` tool_result (paired on `tool_use_id`) | **2 uses, 2 answered, 0 orphaned** | Both recovered in full — see below. |
| 6. Dispatcher relays | 14 cross-session messages | 1 carries operator authority: *"operator approved `allow_update_branch=true` (now live)"* (DISPATCHER 4, 18:58Z) — already banked in `main-branch-protection-via-ruleset.md`. |

## Recovery 1 — the operator's Q1 answer was free text, richer than the summary's paraphrase

AskUserQuestion `toolu_01Wx7Xb13pDQ4bBXVYkqkgRD` (00:35Z). The operator did **not** pick any offered
option for the banner question. Verbatim:

> "I mean more like, let's just replace the classic where we have places in the new one. Let's just
> not expose entries to those pages, removing them if possible once we know we have 100% feature
> coverage. The ideal is that we force migrate people to the new UI so we can stop maintaining the
> old one."

The summary kept only the last sentence. The operative instruction is the middle one: **stop exposing
entry points/links to classic pages that already have a new-UI equivalent, and remove those pages
once parity is proven.** That is a concrete next step, gated on the #1955 parity audit — and it is
*hiding entry points*, which is weaker and more reversible than the "removal is NOT authorized"
line in the brief. Do not conflate the two.

Q2 answer: "Discover + the other gear-menu toggles (Recommended)" — shipped in #1956.
Q3 answer: "Ideally we can redirect to pages that have a new UI equivalent but we must have 100%
coverage and i mean true 100% coverage."

## Recovery 2 — `state/MISSION.md` "Now / next action" is stale

It still reads "PR #1956 is open, rebased onto main ... awaiting its Test Suite run". All three PRs
merged hours ago. Corrected in the same commit as this file.

## Confirmed unchanged (no action)

- Both AskUserQuestion menus were answered; **no question died silently** across either compaction.
- All three merge SHAs verified ancestors of `origin/main` (`fa294a1ab6`): 928b715986 (#1956),
  2dd1df61b7 (#2023), 194b9c1937 (#2021).
- No background legs of mine survived the reboot (uptime 5:36). The two live `codex` processes
  belong to other sessions (AppleWatchOrrery; a CWNG issue-1393 leg). Nothing to re-dispatch.
- KINDLE KIDS' "gh pr update-branch cancels auto-merge" — relayed by DISPATCHER 4 at 18:58Z and
  **retracted by KINDLE KIDS at 19:53Z** after it ran my discriminator. Do not cite it.

## Canary verification (2026-08-30T00:44Z) — closes the brief's last ASSUMED link

The brief's done condition required evidence "on the dev container via CARE". CARE's deploy was
confirmed by merge-base only (code is in the build); behaviour on the canary was never exercised.
Now OBSERVED, read-only, over an SSH tunnel to teenyverse (`127.0.0.1:18083`, container
`calibre-web`).

**Two instrument failures were caught before they became findings — both by controls:**

1. `docker inspect`'s `image.revision` label reads `af9a05d0adf…`, which is **not a ref in our
   repo** (`upload-pack: not our ref`), so `git merge-base --is-ancestor` returned ABSENT for both
   product SHAs. A positive control against a known ancestor proved the *test* worked, so ABSENT
   meant "unknown commit", not "not deployed". This is the lag
   `[[live-container-app-path-and-the-absent-trap]]` already warns about — verify by grepping the
   container, never from the revision label.
2. My first HTTP matrix sent **no browser headers at all**: zsh does not word-split an unquoted
   `$NAV`, so the whole header string went to curl as one argv element. Every "browser" row was
   silently the machine-client path, and the rows agreeing was the tell. Fixed with a real array.
3. The first opt-out test read a `-c` cookie jar, which only records cookies the *server* set — an
   untouched cookie is absent either way, so "revoked" was unfalsifiable. Fixed by seeding a
   Netscape jar and reading the actual `Set-Cookie` headers, with a control request (`/opds`)
   proving a seeded jar survives a request that should not touch it.

**Deployment, grepped inside the container** (`/app/calibre-web-automated/cps`, 90 `.py` files;
controls `_SAFE_PREFIX_RE` and `spa_shell_url` PRESENT, so the instrument works):
`cwng_prefer_classic` PRESENT · `user_preferences.py` PRESENT · `cwng_switch` PRESENT ·
`_explicit_spa_choice_requested` PRESENT.

**Behaviour, cookie-less, real navigation headers:**

| probe | result | meaning |
|---|---|---|
| browser `GET /login` | 302 → `/app/` | new UI is the default |
| machine `GET /login` (`Accept: */*`) | 200 classic | machine clients untouched |
| browser `GET /` | 302 → `/login?next=/` | auth-required instance; chain lands on SPA |
| `/opds` with FULL browser nav headers | 401 | gate does not hijack OPDS |
| `/kobo/<bad>/v1/library/sync` browser | 401 | Kobo untouched |

**Operator's #2023 ruling, OBSERVED on the canary:**

| probe | `Set-Cookie` | opt-out | next `/login` |
|---|---|---|---|
| `/app/book/5` (deep link, opt-out held) | only `cwng_prefer_spa=1` — **no deletion** | PRESERVED | 200 classic |
| `/app/?cwng_switch=spa` (explicit control) | `cwng_prefer_classic=; Max-Age=0` + spa stamp | REVOKED | 302 → `/app/` |

That is exactly "only an explicit action revokes it", live.

**Still ASSUMED, deliberately:** "Discover preference follows the ACCOUNT across two browsers" is
OBSERVED locally (E2E 9/9) but NOT on the canary — verifying it needs an authenticated session on
the operator's real instance and would mutate their account preferences. It belongs to the operator's
own exercise pass (CARE staged five paths for them). Do not self-serve this one.
