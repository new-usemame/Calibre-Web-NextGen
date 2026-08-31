# Mission: #1942 M2 annotation seeding pipeline

Updated: 2026-08-29
Phase: closure correction R5 implementation
Status: 3/3 R5 outcomes done and verified

## R5 definition of done (surgical F2 closure)

- [x] A snapshot stores its exact `authority_revision` and `set_digest`, and replay requires both values to equal current book state.
- [x] Every locally acknowledged authoritative PATCH first commits an authority revision increment plus set-digest/current-ETag invalidation; failure withholds the 204.
- [x] The exact `{A}` GET → `{B}` PATCH → dual-read-failure ordering cannot replay `{A}`; a newer complete live render survives snapshot-commit failure; a pre-R4 authority row without a snapshot reaches only the loud terminal fallback.

## R4 definition of done (closure FIX-FIRST verdict)

- [x] A prior CWNG ETag on an ever-authoritative book is answered locally with the current complete set, HTTP 200, and a CWNG ETag; no ever-authoritative GET branch proxies Kobo.
- [x] Every successful complete local render durably snapshots its exact body; dual live-query failure replays the validated snapshot, readable membership failure never emits an invented empty set, and no-snapshot authority failure has only the explicit loud 503 terminal fallback.
- [x] An initial divergent same-ID reconciliation conflict retains both candidates, is rejected/quarantined through the authenticated recovery path, and cannot promote into an older local replacement response. Named serial regressions, focused/full suites, Ruff, and `git diff --check` pass.

## R3 definition of done (closure FIX-FIRST verdict)

- [x] A new/reset device's download-shaped first GET on an ever-authoritative book commits device-specific `routing_only` evidence before rendering; a request carrying prior CWNG-possession evidence proxies instead, never 503.
- [x] Ever-authoritative GETs never return 503: corrupt capture evidence is rebuilt from the complete live set, and authoritative recovery is supported beyond the quarantined-only path.
- [x] `ever_authoritative` is tri-state at GET and PATCH routing boundaries; lookup failure is never treated as never-authoritative or silently swallowed.
- [x] Post-authority growth beyond 100 still acks the device PATCH, advances the local revision, flags `oversize_single_page`, and serves the complete local set in one response with the pending-hardware boundary documented.
- [x] Reconciliation uses a durable server-owned per-row revision baseline/CAS; divergent rows keep local content and surface the capture conflict. Named regressions, serial focused/full suites, Ruff, and `git diff --check` pass.

## R2 definition of done (FIX-FIRST verdict)

- [x] `ever_authoritative` is sticky across the paired GET/PATCH decision: after local PATCH starvation starts, no owned GET can proxy a stale Kobo replacement set.
- [x] Promotion and render prove every captured annotation ID is included in the visible served ID set; cardinality equality alone cannot promote or serve.
- [x] Captured raw sidecars become serveable only when the captured content was applied or is content-equivalent to the current generic row.
- [x] Reconciliation protects newer server/browser edits using server-side revision/modification evidence, including equal Kobo client clocks.
- [x] A later device's capture failure is device-scoped and cannot globally quarantine an already-authoritative book.
- [x] An explicit authenticated production API can recover/retry a genuinely quarantined book.
- [x] Abandoned pending captures expire/restart, including a dead paginated cursor returning to a first-page request.
- [x] SQLite enforces one reconciliation owner per user/book and reconciliation rejects captures whose starting authority revision is stale.
- [x] Named regressions cover every reproduced review sequence; focused suites, Ruff, `git diff --check`, and the full unit suite have fresh exact evidence.

## Now / next action

Commit the verified R5 correction atop exact base `f5db7afead28485afc5991dac94549744cacd789` with the required identity and create/verify `1942-m2-r5.bundle` at the worktree root.

## Verification commands

- Focus: project test command discovered from existing test documentation/configuration, limited to `tests/unit/test_1942_seed_pipeline.py` and `tests/unit/test_1923_owned_annotations_local_authority.py`.
- Migration: run the repository's existing migration test path against fresh and pre-column schemas, including an idempotent second pass.
- Full unit suite: run the repository's normal full unit command; record passed/failed/error/skipped counts and classify only evidenced sandbox-infrastructure failures separately.
- Git: `git diff --check`; inspect scoped diff; verify commit author and remote owner before push/bundle.

## Decisions and rationale

- 2026-08-29 R5: closure review `/tmp/cwng-m2-review4-last.txt` was read completely (147 lines); F1 and F3 are confirmed closed, while the reproduced GET `{A}` → PATCH `{B}` → dual-read-failure sequence proves R4's unversioned snapshot can wipe `B`.
- 2026-08-29 R5: snapshots are mutation proofs, not time-based caches. Exact replay now requires stored/current authority revision equality and stored/current set-digest equality in addition to gzip/SHA/count/JSON integrity.
- 2026-08-29 R5: after local annotation persistence, the PATCH route commits a revision increment and clears `set_digest`/`current_etag` before emitting the byte-exact 204. Clearing is deliberate: until a complete post-PATCH body is rendered, fabricating a body digest or ETag would be dishonest.
- 2026-08-29 R5: a complete live body outranks durability failure. If its snapshot commit fails, return that known-complete body with a body-derived transient ETag; never substitute older snapshot membership.
- 2026-08-29 R5 design boundary: review4's stronger bar says PATCH must not 204 until a complete new fallback representation is itself durable, while the manager explicitly chose revision invalidation followed by terminal 503 when live reads fail. The newer manager instruction governs this surgical round; the disagreement is recorded rather than silently treated as review-level closure.
- 2026-08-29 R5: the M3 briefing is stale since 2026-06-12 and reports no #1942 escalation; the direct manager brief is the current source of truth.

- 2026-08-29 R4: closure review `/tmp/cwng-m2-review3-last.txt` was read completely (250 lines); all three fail-wrong paths are present at `a8cf4694d` and reopen the mission.
- 2026-08-29 R4: the R3 prior-CWNG-ETag proxy decision was wrong. A CWNG ETag is affirmative possession of CWNG's set, so every ever-authoritative GET remains local; `If-None-Match` never produces 304.
- 2026-08-29 R4: a failed live-row query is unknown, never proof of an empty set. Persist exact served bytes as a validated gzip+SHA snapshot and prefer stale-but-CWNG replay over invented empty or stale Kobo data. Without any prior snapshot, a loud 503 is the explicitly authorized terminal authority fallback.
- 2026-08-29 R4: a server CAS proves mutation ordering, not which divergent baseline candidate is newer. Initial unresolved divergence blocks promotion and retains the local row plus captured page evidence for authenticated recovery.
- 2026-08-29 R4: review3's separate Account UI discoverability and oversize diagnostic/revision bookkeeping observations are valid but outside the user's explicit three-path R4 scope; they are not silently represented as closed.

- 2026-08-29 R3: closure review `/tmp/cwng-m2-review2-last.txt` was read completely (262 lines); it reopens the mission at exact base `7dc4375fdf346453ed287d75cd8e4f937584ce4c`.
- 2026-08-29 R3: hardware-observed `notes/KOBO-HIGHLIGHTS-STATE.md` §6p establishes the download/re-download GET lifecycle used for pre-serve `routing_only` evidence. A prior CWNG ETag is possession evidence and fails safe to status-quo proxy, never an error response.
- 2026-08-29 R3: the manager's post-authority oversize decision deliberately supersedes the nominal M2 `≤100` wire cap: local PATCH remains lossless, the book is flagged, and GET returns the complete set in one page. Nickel acceptance is ASSUMED pending the Clara A/B probe.
- 2026-08-29 R3: corrupt historical capture bytes cannot invalidate the current live authoritative set. Recovery rebuilds proof from live rows and preserves local authority; stale Kobo proxy and GET error responses remain forbidden after authority.
- 2026-08-29 R3: reconciliation conflicts are resolved by server-owned row revision evidence, never the client clock. CAS divergence keeps the local row and records a privacy-safe conflict signal.
- 2026-08-29 R3: the new baseline table participates in explicit child-first privacy purge; the adjacent suite caught and verified this integration requirement before delivery.
- 2026-08-29 R3 delivery: the sandbox rejected the requested parent path `../1942-m2-r3.bundle`; the verified complete-history fallback is `1942-m2-r3.bundle` in this writable worktree root.

- 2026-08-29 R2: adversarial review `/tmp/cwng-m2-review-last.txt` was read completely (318 lines); its FIX-FIRST verdict reopens the mission and invalidates the prior merge-ready claim.
- 2026-08-29 R2: when the review leaves behavior open, choose fail-safe proxy/status-quo behavior only if the cloud has not yet been starved; once `ever_authoritative=1`, paired GET/PATCH stickiness forbids stale upstream replacement-set GETs.
- 2026-08-29 R2: CWNG briefing is stale since 2026-06-12. Git Manager hydration is unavailable because the broker is down and managed DNS cannot resolve the board; direct operator instructions are the governing authority.
- 2026-08-29 R2: a new/reset device on an ever-authoritative book receives the complete local set and records `routing_only` acceptance; it never recaptures the already-starved Kobo cloud.
- 2026-08-29 R2: if an ever-authoritative local GET cannot prove/render a complete set, fail closed locally with 503 rather than proxy stale cloud bytes. Before first authority, the status-quo proxy remains the fail-safe.
- 2026-08-29 R2: accepted upstream capture pages are durable identity evidence. A missing identity is safe only when the retained hidden row has a server modification later than the capture completion (an explicit newer authoritative tombstone).
- 2026-08-29 R2: captured content may update an existing row only when server evidence does not outrank it; exact content equivalence can still make its raw sidecar serveable. Browser edit/delete paths now advance `content_revision` and `server_modified_at`.
- 2026-08-29 R2: initial quarantine recovery returns to `unseeded`; historical `ever_authoritative` quarantine returns to local `authoritative`, because cloud fallback is no longer safe.
- 2026-08-29 R2: pending captures expire after 15 minutes. A same-device first-page request immediately supersedes a pending chain whose continuation cursor will not be used.
- 2026-08-29 R2: one `result='pending'` row per book-state is enforced with a SQLite partial unique index; migration retires duplicate legacy owners before creating it. Every capture snapshots and rechecks `authority_revision`.
- 2026-08-29 R2 delivery: correction commit uses the exact `new-usemame` noreply identity. Push was attempted and blocked by managed DNS (`github.com` could not resolve). The sandbox rejected the requested parent path `../1942-m2-r2.bundle`; the verified complete-history fallback is `1942-m2-r2.bundle` in this writable worktree root.

- 2026-08-29: preserved unrelated completed `state/MISSION.md`; this mission uses a task-specific ledger.
- 2026-08-29: capture all upstream pages, but quarantine any capture requiring more than the one page local authority can serve.
- 2026-08-29: implement the spec's sticky design: `ever_authoritative=1` permanently prevents later owned PATCH forwarding.
- 2026-08-29: hardware Clara verification is deliberately deferred until after merge, per operator scope.
- 2026-08-29: Git Manager hydration unavailable because the secrets broker is down; no credential fallback attempted.
- 2026-08-29: the all-devices calculation intentionally means active `kind='kobo'` devices. Browser devices cannot originate the Kobo annotations GET that supplies a capture; this matches the M2 instruction to capture each other active Kobo on its next GET.
- 2026-08-29: no M3-M5 or entitlement-replay code was changed; hardware verification remains post-merge.
- 2026-08-29: implementation committed as `ae36f43bac2ce11fc3b4a210fefe6ed157e1df4c` with the exact `new-usemame` identity. Push was attempted and blocked by managed DNS (`github.com` could not resolve), so delivery uses a verified complete-history bundle. The sandbox cannot write the requested parent-directory spelling; the final artifact is `1942-m2.bundle` in this writable worktree root.
- 2026-08-29: final spec/runbook audit added capture-derived W1 opaque evidence in `c407da24fe`: a complete set records `absent` only when attachments prove it, records `present` when observed, and never downgrades durable prior `present` evidence.

## Evidence classification

- OBSERVED R5: exact delivery base is `f5db7afead28485afc5991dac94549744cacd789`; review4's GET `{A}` → local PATCH `{B}` → dual-query failure sequence reproduced the stale-snapshot subset before correction.
- OBSERVED R5: the named M2 pipeline suite passes 36/36 serially. It proves snapshot revision/digest equality, PATCH revision advancement before exact 204, stale-snapshot rejection after PATCH, live `{A,B}` preservation when snapshot commit fails, no-snapshot upgrade fallback, and 204 withholding when the authority commit fails.
- OBSERVED R5: existing #1923 authority passes 17/17 and the authenticated Kobo two-way API passes 28/28 serially (81/81 required focused tests total).
- OBSERVED R5: adjacent Stage-0, sync-schema, privacy/purge, and scope-migration suites pass 67/67 serially; PATCH spool/containment/clock suites pass 89/89; the exchange-capture local GET/PATCH pair passes 2/2 after explicitly mocking the new authority-commit seam.
- OBSERVED R5: final-tree full unit suite collected 7,629 tests: 7,506 passed, 106 skipped, and 17 failed in 221.57s. The 17 are the known managed-sandbox infrastructure set: 10 loopback socket-bind denials and 7 `ps` execution denials; no application assertion failed.
- OBSERVED R5: scoped Ruff across every changed small implementation/test file passes, fatal-error Ruff across the two touched legacy runtime files passes, Python compilation and `git diff --check` pass.
- ASSUMED R5: per the manager decision, a post-PATCH dual live-read failure with no revision-matched snapshot uses terminal 503. Review4 explicitly regards 503 as insufficient and instead requires a new durable fallback before PATCH 204; that stronger behavior is not claimed.

- OBSERVED R4: exact delivery base is `a8cf4694dc711577fe527c4c9a61fd6f266940ba`; all three closure-review fail-wrong paths reproduced before correction.
- OBSERVED R4: the named M2 pipeline suite passes 33/33 serially, including prior-CWNG-ETag local 200 plus paired PATCH, exact snapshot replay after both live queries fail, nonempty snapshot replay on readable-membership failure, explicit no-snapshot terminal 503, and the inverse-conflict quarantine/recovery sequence.
- OBSERVED R4: existing #1923 authority passes 17/17 and the authenticated Kobo two-way API passes 28/28 serially (78/78 required focused tests total).
- OBSERVED R4: adjacent Stage-0, sync-schema, privacy/purge, and scope-migration suites pass 67/67 serially.
- OBSERVED R4: final-tree full unit suite collected 7,626 tests: 7,503 passed, 106 skipped, and 17 failed in 271.19s. The 17 are the known managed-sandbox infrastructure set: 10 loopback socket-bind denials and 7 `ps` execution denials; no application assertion failed.
- OBSERVED R4: scoped Ruff on the implementation/regression files passes, fatal-error Ruff across the two touched legacy files passes, Python compilation and `git diff --check` pass. Broad whole-file Ruff reports 12 pre-existing legacy findings outside the changed hunks.
- ASSUMED R4: no hardware behavior is claimed; the Clara oversize-page A/B remains post-merge as previously scoped.

- OBSERVED R3: exact delivery base is `7dc4375fdf346453ed287d75cd8e4f937584ce4c`.
- OBSERVED R3: all five closure outcomes are implemented and covered by named regressions for pre-render routing proof, prior-CWNG-ETag proxy, corrupt-proof live rebuild plus authenticated recovery, tri-state GET/PATCH routing, lossless 101-row response, clock-skew local-wins, and post-page-commit CAS divergence.
- OBSERVED R3: `tests/unit/test_1942_seed_pipeline.py` passes 29/29 serially.
- OBSERVED R3: required focused compatibility passes 74/74 serially (`#1942`, existing `#1923`, authenticated Kobo two-way API).
- OBSERVED R3: adjacent schema, Stage-0, scope-migration, and privacy-purge verification passes 67/67 serially.
- OBSERVED R3: final full unit suite collected 7,622 tests: 7,499 passed, 106 skipped, 17 failed, 6,165 warnings in 242.54s. All 17 are the known managed-sandbox infrastructure set: 10 loopback socket-bind denials and 7 `ps` execution denials; no application assertion failed.
- OBSERVED R3: scoped Ruff on the new/small implementation and regression files passes; fatal-error Ruff across every changed Python file passes; Python compilation and `git diff --check` pass. A broad whole-file Ruff invocation still reports 13 pre-existing legacy findings outside the changed hunks.

- OBSERVED R2: exact base is `e4be9b4df1bb54afa50aa3cfaa0e5a7276799467`; worktree has no code modifications at reopen.
- OBSERVED R2: review reproduced paired-authority inversion, membership-substitution promotion, stale-sidecar serving, global quarantine from later-device failure, dead paginated capture stranding, and missing logical reconciliation serialization.
- OBSERVED R2 at reopen: all nine R2 boxes were unchecked; prior green evidence was historical and could not establish the corrected branch.
- OBSERVED R2 final: all nine R2 boxes are checked against named regression or schema evidence.
- OBSERVED R2: `tests/unit/test_1942_seed_pipeline.py` passes 21/21 and includes named reproductions for both paired-authority inversions, promotion and render membership substitution, stale sidecar, equal-clock browser edit, later-device failure isolation, authenticated recovery, dead cursor/TTL restart, SQLite ownership, and stale authority revision.
- OBSERVED R2: required focused compatibility suites pass 66/66 (`#1942`, `#1923`, Kobo two-way API). Adjacent edit/delete, vocabulary, sync-helper, and schema suites pass 134/134.
- OBSERVED R2: final full unit suite collected 7,614 tests: 7,491 passed, 106 skipped, 17 failed, 6,165 warnings in 275.97s. All 17 failures are the known managed-sandbox infrastructure set: 10 loopback socket-bind denials and 7 `ps` execution denials. No application assertion failed.
- OBSERVED R2: scoped Ruff passes; fatal-error Ruff across every changed Python file passes; `git diff --check` passes.

- OBSERVED: operator supplied the M2 objective, exact scope, done conditions, identity, and hardware deferral.
- OBSERVED: required spec and seeding runbook were read; their M2, migration, completeness, C1-C4, wire, and replay constraints govern this mission.
- OBSERVED: branch starts clean as `feat/1942-m2-seeding...origin/main` with M1 merged.
- OBSERVED: M3 briefing is stale (2026-06-12) and records zero unresolved escalations at its last update.
- OBSERVED: focused final verification passed 27/27 (`test_1942_seed_pipeline.py`, #1923, and raw materialization regression).
- OBSERVED: adjacent schema/route/persistence verification passed 98/98 before the final focused run; production migration replay and classifier follow-ups passed 3/3.
- OBSERVED: final full unit suite: 7,479 passed, 106 skipped, 17 failed in 220.03s. All 17 failures are sandbox infrastructure: 10 loopback socket-bind denials and 7 `ps` execution denials; no application assertion remains failed.
- OBSERVED: Python compilation, focused Ruff on the new service/tests and adjacent small modules, and `git diff --check` pass.
- ASSUMED: no additional private Git Manager directive conflicts with the direct operator brief; hydration was unavailable because the secrets broker was not running.
