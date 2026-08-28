# Issue #1925 — Kobo sync de-download report

Status: two-layer implementation complete; regression-loop round 1 found and
fixed one admin-resend lifecycle defect; focused and complete executable-unit
verification green; Clara hardware discrimination pending.

## Mechanism

- **OBSERVED — discriminating integration tests:** real `HandleSyncRequest`
  calls with one user, one unchanged book, and real SQLAlchemy cursor queries
  reproduce the replay. With Layer 2 off, two tokenless requests both contain
  the same `NewEntitlement`, but Layer 1 makes the two payloads byte-identical.
  With Layer 2 on, a successfully parsed CWNG token whose cursors are behind
  selects the book but the matching per-device fingerprint suppresses it.
- **OBSERVED — the token is the replay trigger:** a normal request that echoes
  the first response's `x-kobo-synctoken` advances past the unchanged book.
  A missing, malformed, truncated, or official-store-only token becomes a
  `SyncToken` with local cursors at `datetime.min`, so the book query selects
  the whole library again. The old handler retained no per-device record of the
  payload already delivered and replayed every selected entitlement.
- **OBSERVED — candidate (1), the empty `KoboSyncedBooks` reset, is sufficient
  but not necessary:** the red test keeps the user's `KoboSyncedBooks` row
  present before request two, so the line-349 full-reset branch does not fire.
  Token loss alone reproduces the server response. If that table is also empty,
  the branch explicitly discards a valid incoming local cursor and reaches the
  same replay path.
- **OBSERVED — candidate (3), cursor/token loss, is the root replay
  mechanism:** the unchanged response is replayed when the local cursor is
  absent or behind the payload already emitted. The already-landed #468 Magic Shelf fix preserves
  `MagicShelfCache.created_at` when membership is unchanged, so an unchanged
  cache rebuild is not the discriminator at this HEAD.
- **OBSERVED — first/second response diff on old code:** the same UUID was
  re-sent as `NewEntitlement`. For requests generated in different clock
  seconds, the sole logically-unchanged entitlement field that mutated was
  `BookEntitlement.ActivePeriod.From`; `DownloadUrls[].Size` stayed numerically
  equal to the source `Data` row but did not describe the generated artifact.
  Thus token loss selects the replay, wall-clock `ActivePeriod` makes its JSON
  unstable, and `Size` can make the stable-looking declaration untruthful.
- **OBSERVED — candidate (2), declared size, is a contributing payload defect,
  not the replay trigger:** `build_download_url` declared
  `book_data.uncompressed_size`. For deferred EPUB→KEPUB conversion that is the
  EPUB's stored size; with download-time metadata embedding, even a stored
  KEPUB is rewritten to fresh bytes. The declared value therefore does not
  describe the artifact Nickel receives. The fix omits `Size` for generated
  KEPUBs and every metadata-rewritten EPUB/KEPUB, retaining it only for exact
  stored files.
- **OBSERVED — another unstable entitlement field:** `ActivePeriod.From` used
  response-generation wall-clock time. Two otherwise identical entitlement
  builds therefore differed even with no library change. It now uses the
  stable book-created timestamp, the same value as `Created`.
- **OBSERVED on hardware (source dossier §6n/§6q):** the abnormal post-firmware
  Clara sync and post-USB-interruption Libra sync re-stamped unchanged `content`
  rows and changed downloaded books to `IsDownloaded='false'`; three of four
  flipped books also changed `___FileSize`; server `metadata.db` had no
  `last_modified` bump.
- **ASSUMED — Nickel's exact decision predicate:** the combined hardware and
  server evidence is consistent with Nickel treating a replayed entitlement,
  especially one whose declared `Size` disagrees with its local artifact, as a
  replacement and clearing `IsDownloaded`. Nickel is closed-source and the
  Python integration test proves the server stimulus, not Nickel's private
  branch condition. The Layer 1 Clara experiment below tests whether stable,
  truthful payloads alone remove the harmful client outcome.

## Layer 1 — ship first: payload stabilization, no suppression

- **OBSERVED:** `ActivePeriod.From` is the stable book-created timestamp, equal
  to `Created`; malformed legacy timestamps use a deterministic epoch rather
  than response time.
- **OBSERVED:** `DownloadUrls[].Size` is omitted for generated KEPUBs and every
  metadata-rewritten EPUB/KEPUB. Exact stored artifacts retain their truthful
  stored size.
- **OBSERVED:** the permanent DEBUG summary reports New/Changed/suppressed
  counts, Layer 2 enabled/eligible state, and in/out book, archive,
  reading-state, tag, and Magic Shelf cursors without logging the opaque store
  token.
- **OBSERVED:** with the new flag at its default `false`, the sync path computes,
  queries, and writes no fingerprint and suppresses no entitlement. A tokenless
  unchanged-library replay is still emitted, now byte-identically. This is the
  default production behavior until Clara evidence justifies Layer 2.
- **OBSERVED:** real `Books.last_modified` changes still emit exactly one
  `ChangedEntitlement`.

## Layer 2 — experimental, default off

- **OBSERVED:** `config_kobo_suppress_replayed_entitlements` is additive,
  defaults false on upgrades and fresh installs, is exposed as an experimental
  admin checkbox, and gates all ledger reads/writes and suppression behavior.
- **OBSERVED:** when enabled, `kobo_device_book_entitlement` stores the SHA-256
  of stable `BookEntitlement` + `BookMetadata` per `(device_id, book_id)`. The
  HMAC-backed device registry resolves the physical device without storing its
  raw hardware identifier.
- **OBSERVED — factory-reset escape:** suppression is eligible only when the
  request has a resolved device and `SyncToken.from_headers` successfully
  decoded and schema-validated a CWNG token. Empty, malformed, truncated, and
  official-store tokens always receive the full replay, even when the hardware
  already has ledger rows. Those deliveries refresh the ledger.
- **OBSERVED:** a valid stale CWNG token plus an exact same-device fingerprint
  suppresses New/Changed entitlement replay while still advancing cursors.
  A second device has no matching row and receives its entitlement.
- **OBSERVED — Layer 2 reading-state isolation:** the committed suppression
  block skipped both the embedded `ReadingState` payload and local
  `reading_state_last_modified` advancement when the base entitlement matched
  the ledger. The later generic reading-state query rescued a simple one-book
  response, but that rescue was page-dependent: a full page of older states
  withheld the suppressed book's newer state and left the outgoing cursor
  behind it. Reading-state serialization, emitted-ID bookkeeping, and cursor
  advancement now happen before the base entitlement suppression branch. A
  suppressed base emits its newer state as `ChangedReadingState`; an
  unsuppressed base retains the existing embedded `ReadingState` shape. Both
  paths exclude that book from the later generic scan, so the state appears
  once and is not re-offered after the returned cursor is echoed.
- **ASSUMED/unavoidable ambiguities:** an empty library that somehow retains a
  valid stale CWNG token is indistinguishable from an interrupted sync whose
  library is intact. Also, the ledger proves that the server generated a
  response, not that the device applied it: if transmission stops before the
  device applies a newly offered book and it retries the same valid token,
  Layer 2 may suppress a book the device never received. The escape covers the
  normal factory-reset/re-setup signature (no valid CWNG token), not retained-
  token database corruption, partial restores, or pre-application response
  loss. These residual risks are why Layer 2 defaults off.
- **OBSERVED:** explicit unsync/resend, full-sync, archive-removal, duplicate
  merge, book purge, user purge, and database-swap paths clear the new ledger
  at the same boundary as the legacy delivery marker. This prevents replay
  suppression from masking an operator-requested resend or a book returning
  from Archive.
- **OBSERVED:** `KoboDeviceBookEntitlement` is registered in
  `PER_USER_BOOK_MODELS`; because its user scope is indirect, purge resolves it
  through `Device`, while duplicate/book purges filter by `book_id`.

## Pre-existing flat-marker constraint

- **OBSERVED:** `KoboSyncedBooks` remains keyed only by `(user_id, book_id)`,
  not device. Its archive/reset logic can establish only that some Kobo for the
  user was offered the book; it cannot establish that this requesting device
  still has it. One device's delivery/removal therefore affects the user's
  flat archive candidate set seen by other devices.
- **OBSERVED:** this is why neither a present `KoboSyncedBooks` row nor the
  physical-device HMAC is a safe non-empty-library discriminator. Layer 2 uses
  its per-device ledger only after a valid returned CWNG token, and Layer 1
  leaves the flat-marker behavior unchanged.
- **ASSUMED/future work:** fully resolving the archive hazard requires making
  delivery/archive state device-scoped or receiving authoritative device
  library state; #1925 does not attempt that migration.

## Dating

- **OBSERVED — earliest payload instability in upstream history:** upstream
  commit `8e1641dac9c9211ef324d5aeb8cfd399cc496bc0` (authored 2020-02-15,
  committed 2020-03-01, “Add support for syncing Kobo reading state”) introduced
  the wall-clock `ActivePeriod` shape. The current CWNG ancestry acquired that
  code in the CWA in-repo “MAJOR REFACTOR” import
  `73eecc175bf241c4410e7e220c7d2bfb426851ce` on 2025-08-02; the first tag
  containing that import is `V3.1.2` (2025-08-03).
- **OBSERVED — size field provenance:** upstream commit
  `55c0bb6d34e009b5aed241037187d17357551432` (2019-12-08) introduced
  `DownloadUrls[].Size` from the stored `Data` row. It was truthful for the
  exact stored KEPUB path at that point.
- **OBSERVED — download-time KEPUB byte instability:** upstream commit
  `b8031cd53fe19ac37f1962f5010b2669e45875d2` (2024-01-13, “Add possibility to
  replace kepub metadata on download”) introduced `updateEpub(...); zf.writestr`
  for every metadata-embedded KEPUB download. Python supplies the new ZIP
  member's current local time, so identical logical input can produce different
  bytes/size. CWNG again acquired this in `73eecc175...` / first tag `V3.1.2`.
- **OBSERVED — first CWNG PR that made the declared-size mismatch apply to
  deferred conversions:** `27b334cc1877655c2086560148a00094582f6591`
  (committed 2026-06-04, authored 2026-06-05), PR **#350**, “defer kepub
  conversion”. It selected the EPUB `Data` row while declaring a KEPUB download
  URL, so the EPUB size was guaranteed to name a different artifact. The first
  release tag containing it is `v4.0.146` (2026-06-04).
- **OBSERVED — cursor-loss replay provenance:** upstream commit
  `25422b341142729fdc6f6d32b45e27986a3d535e` (2021-12-12, fix for upstream
  #2195) added the empty-`KoboSyncedBooks` forced-reset branch. Malformed/foreign
  token fallback has existed since the early SyncToken implementation; neither
  path had per-device payload memory.
- **OBSERVED — shipped scope:** the previous stable release `v4.1.41`
  (`VERSION` = `4.1.41`, tagged 2026-08-25) contains the import, PR #350, token
  reset, and download-time rewrite, so it carries the complete #1925 exposure.

## Automated verification

### Red on old code

Command:

```text
python -m pytest -q tests/unit/test_1925_kobo_sync_dedownload.py
```

Manager-verified result against old code for the nine-test regression revision:
**6 failed, 3 passed**. The failures discriminated replay suppression, unstable
timestamps, and untruthful generated/rewritten download sizes from the positive
controls that old code already handled.

- replay assertion failed: second response contained one unchanged
  `NewEntitlement`;
- stable-field assertion failed: `ActivePeriod.From` was wall-clock time rather
  than `Created`;
- generated-KEPUB assertion failed: response declared the source EPUB size;
- real `last_modified` bump assertion already passed.

**OBSERVED — Layer 2 reading-state red:** the exact new assertion
`test_suppressed_entitlement_emits_newer_reading_state_once_and_advances_cursor`
was run against the committed suppression block with the generic state page
filled by one older state (the test pins `SYNC_ITEM_LIMIT=1`). The suppressed
book's newer target state was absent: `assert len(target_states) == 1` failed
with `0 == 1`. This distinguishes the direct-emission fix from the generic
fallback that makes the unsaturated one-book case pass.

### Green after implementation

**OBSERVED — original focused two-layer, lifecycle, and adjacent KEPUB
selection:**

```text
python -m pytest -q \
  tests/unit/test_1925_kobo_sync_dedownload.py \
  tests/unit/test_kobo_synctoken_validation.py \
  tests/unit/test_kobo_synctoken_compression_331.py \
  tests/unit/test_kobo_annotation_stage0.py \
  tests/unit/test_user_book_data_d4.py \
  tests/unit/test_kobo_admin_resend_book.py \
  tests/unit/test_kobo_prefer_kepub.py
```

Current result: **103 passed**. The #1925 module contributes 16 collected cases,
including default-off/zero-ledger behavior, byte-identical tokenless replay,
valid-stale-token suppression, absent/malformed/store-token factory-reset
escapes, per-device isolation, real-change positive control, stable timestamps,
truthful size behavior, schema creation, config migration/defaults, and strict
suppression provenance atop permissive legacy token parsing. The new case also
pins that a suppressed base entitlement emits its newer reading state once,
advances the outgoing reading-state cursor to that timestamp, and does not
re-offer it on the next sync. The D4 suite pins `PER_USER_BOOK_MODELS`
registration and lifecycle cleanup.

**OBSERVED — final complete executable unit suite:** `tests/unit` collected
**7,363** tests. With the 17 environment-blocked node IDs below explicitly
deselected, the post-reading-state-fix result was **7,244 passed, 102 skipped,
17 deselected**. No executable unit test failed.

The 17 deselections are existing tests whose required OS operation is denied by
this managed sandbox:

- six real-server cases in `test_gevent_wsgi_format_request.py` — loopback
  `bind(('127.0.0.1', 0))` raises `PermissionError(EPERM)`;
- one case in `test_health_probe_responsiveness.py` — same loopback-bind denial;
- three cases in `test_measure_kobo_patch_failure_is_safe.py` — same
  loopback-bind denial;
- seven cases in `test_s6_ingest_service_shutdown.py` — executing `/bin/ps` to
  inspect the child session raises `PermissionError(EPERM)`.

**OBSERVED — earlier literal repository-default command:** `python -m pytest` collected
7,569 tests and reached **7,348 passed, 123 skipped, 18 failed, 80 setup
errors** before the final test-precondition pin. Seventeen of the failures are
the sandbox-denied unit cases above. The eighteenth was the existing stored-
KEPUB size test relying on uninitialized-config order; it is now pinned to its
actual precondition (`config_embed_metadata=False`) and passes both focused and
in the complete executable unit run. The 80 setup errors are environment-backed
Docker/ingest/KOReader integration suites with no Docker container or live
service in this offline worktree, not product assertion failures. The literal
default command was not rerun after that test-only precondition correction;
the complete unit tree was.

**OBSERVED — static hygiene:** `git diff --check` exits cleanly.

## Regression loop round 1 — 2026-08-28

This round adversarially exercised every surface named in the manager handoff,
including the shelf-only reader's request shapes. The new tests are regression
guards: each asserts an externally relevant invariant that would fail if the
Layer 1/Layer 2 diff disturbed that surface. Where an old-code comparison was
meaningful, it was run explicitly; unchanged-behavior guards are expected to
pass the old implementation and are positive controls, not false red tests.

| Surface | OBSERVED finding and discriminator | Fixed? / verdict |
| --- | --- | --- |
| Shelf-only sync, membership, remove/archive, #468 fail-safe | Real `HandleSyncRequest` tests run two unchanged shelf-only syncs, add a membership and require exactly one delivery, remove it and require the existing `ChangedEntitlement`/`IsRemoved=true` plus marker cleanup, then make Magic Shelf membership unreliable and require no removal and no marker loss. Any replay loop, lost shelf cursor, altered removal command, or fail-safe regression fails these assertions. | No defect found. All guards green. |
| Deferred and rewritten downloads, including `NETWORK_SHARE_MODE` | Entitlement assertions require `Format`, `Url`, `Platform`, and `DrmType` to remain complete while `Size` is omitted only for generated/rewritten artifacts. Six real Flask download-route cases cover deferred EPUB→KEPUB, rewritten stored EPUB, and rewritten stored KEPUB with network-share mode both off and on; each requires HTTP 200, expected bytes, and filename. | No product defect found. The test harness initially lacked `config_unicode_filename` and attempted to read a direct-passthrough response; both were test-fixture corrections, not application changes. All six route shapes green. |
| Legacy, partial, and store-only tokens | Parser tests omit the additive #1925 fields, omit core fields selectively, pass an official-store-only token, and run an actual store-token sync. They require preserved supplied cursors, sane `datetime.min` defaults, correct `is_cwng_token` provenance, no exception, and a valid outgoing response. | No defect found. All guards green. |
| Second physical device, Layer 2 off/on | With Layer 2 off, two devices on one account each get an initial entitlement, subsequent cursors terminate, and no ledger row is written. With Layer 2 on, a fingerprint for device A cannot suppress device B's initial entitlement. | No cross-device defect found. All guards green. |
| Reading state, unsuppressed and suppressed | The unsuppressed positive control requires the pre-change shape: one embedded `ReadingState`, the exact outgoing reading cursor, then no repeat. The suppressed shelf-only discriminator fills the generic-state page with an older state and requires the suppressed book's newer `ChangedReadingState` once with cursor advancement. Temporarily restoring the old suppression block made exactly this test fail (`0` target states instead of `1`); the other **37/38** #1925 cases passed. | Previously identified reading-state defect remains fixed; no new regression. |
| Magic Shelf, sync-all and shelf-only | Parameterized real-sync tests require a Magic Shelf membership change to emit once in both modes and then terminate. The full adjacent Magic Shelf suite covers cache reset, large-shelf sub-cursors, local cursor reuse, full-page deferral, and ID-list membership. | No defect found. All guards green. |
| Permanent DEBUG sync summary | A store-token/min-cursor request and direct nullable-cursor formatting exercise the log path. Assertions require a 200 response, one summary record, stable count fields, and no exception for `None`/minimum cursor shapes. | No defect found. All guards green. |
| Full sync, resend, unsync, purge, duplicate merge | Full sync and unsync were correctly scoped; D4 purge/merge/registry tests remained green. A new behavioral resend test seeded two target-user devices plus another account, invoked admin resend, then synced: before the repair, both target ledger rows remained and the test failed. | **Finding R1-1 fixed:** `do_kobo_resend` now clears the requested book's fingerprints for every device belonging to the target user, preserves other users/books, validates the book before mutating either database, and allows the next speaking device to receive and re-seed the entitlement. |

### Round-1 red/green evidence

- **OBSERVED — R1-1 red before repair:** the lifecycle subset reported **1
  failed, 2 passed**. `test_admin_resend_clears_target_users_entitlement_ledger`
  expected only the unrelated account's row but observed all seeded rows still
  present. After the repair, the expanded lifecycle subset reported **4
  passed**, including the nonexistent-book no-mutation guard.
- **OBSERVED — reading-state old-block comparison:** with only the committed
  pre-fix suppression block restored temporarily, the complete expanded #1925
  module reported **1 failed, 37 passed**. The sole failure was
  `test_suppressed_entitlement_emits_newer_reading_state_once_and_advances_cursor`;
  after restoring the fix, the module reported **38 passed**.
- **OBSERVED — old-code applicability:** the 37 passing old-block cases are
  positive controls for unchanged behavior. Reverting all of Layer 1/Layer 2
  is not meaningful for ledger lifecycle guards because the model and config
  gate do not exist there; the earlier implementation red remains the manager-
  verified **6 failed, 3 passed** result documented above.

### Round-1 green counts

- **OBSERVED — touched-surface focused matrix:** **243 passed**. It includes the
  38-case #1925 module plus the pre-existing resend, D4 lifecycle, book-modified,
  #468, shelf-only archive, Magic Shelf, SyncToken, prefer-KEPUB, real download-
  route, and metadata-rewrite suites.
- **OBSERVED — complete unit tree:** `tests/unit` collected **7,418** tests;
  with the same 17 managed-sandbox node IDs explicitly deselected, **7,299
  passed, 102 skipped, 17 deselected** in 209.83 seconds. The sandbox-blocked
  list remains six gevent loopback binds, one health-probe loopback bind, three
  Kobo measurement loopback binds, and seven `s6` process-tree inspections
  requiring `/bin/ps`.
- **OBSERVED — final clean pass, `Regression-loop round 1 / clean pass 2`:**
  the complete 243-test touched-surface matrix was rerun after the repair and
  report audit; **243 passed** in 5.55 seconds with **zero new findings**.

**ASSUMED:** these executable tests cover the server contracts and route bytes,
not Nickel's closed-source response to the payload. The Clara database-snapshot
procedure remains the hardware discriminator.

## Clara hardware-verification recipe

### Phase A — decide whether Layer 1 is sufficient

Run this phase with `config_kobo_suppress_replayed_entitlements = false`.
Layer 2 must remain off throughout the experiment.

1. Deploy the fixed image, confirm the flag is off, and enable DEBUG logging.
2. With the Clara idle and disconnected from USB, pull baseline
   `KoboReader.sqlite` (`A0`). Export the `content` identity/state tuple for all
   CWNG books:

   ```sql
   SELECT ContentID, IsDownloaded, ___SyncTime, ___FileSize
   FROM content
   WHERE ContentType = 6
   ORDER BY ContentID;
   ```

   Also snapshot server `metadata.db` book id/uuid/`last_modified` for the same
   set.
3. Clear only the target Kobo user's `KoboSyncedBooks` rows. Do not clear books,
   download files, reading state, or device records. This forces the existing
   full-resync branch while Layer 2 is provably inactive.
4. Run one completed sync. Preserve its DEBUG summary; it must show Layer 2
   `enabled=False eligible=False`, `suppressed_unchanged=0`, and a full set of
   local New entitlements. Pull `A1` after the device returns idle.
5. Compare A0→A1 by `ContentID`. For every book whose server `last_modified`
   stayed constant, record:

   - zero `IsDownloaded: true → false` transitions;
   - zero `___SyncTime` changes;
   - zero `___FileSize` changes;
   - the exact replayed entitlement envelope and DEBUG counts.

**Decision gate:**

- If A0→A1 has zero flips and zero row re-stamps, **OBSERVED hardware result:
  Layer 1 is sufficient**. Ship with Layer 2 off; do not enable the experimental
  ledger merely because it exists.
- If a byte-stable replay still flips/re-stamps unchanged rows, **OBSERVED
  hardware result: Layer 1 is insufficient**. Only then proceed to Phase B.

### Phase B — optional Layer 2 verification after an adverse Phase A

1. Enable `config_kobo_suppress_replayed_entitlements`.
2. Force one tokenless/full delivery to seed the per-device ledger. Confirm the
   request is `eligible=False`, all books are delivered, and ledger rows exist.
3. Use **N = 5 completed syncs**, with one deliberately interrupted attempt
   between completed syncs 2 and 3, preserving every DEBUG summary and database
   snapshot B0–B5. The recovery must present a valid stale CWNG token to test
   suppression; if it presents no valid CWNG token, full delivery is the
   intentional factory-reset-safe behavior and does not test Layer 2.
4. For a valid stale-token recovery, require `enabled=True eligible=True`,
   `suppressed_unchanged > 0`, zero local New/Changed envelopes for those exact
   matches, and zero `IsDownloaded`, `___SyncTime`, or `___FileSize` changes.
5. Repeat one request without a CWNG token and require the opposite safety
   behavior: `eligible=False`, no suppression, and a full replay.
6. Positive control after B5: bump `last_modified` on one disposable downloaded
   book, run one additional sync, and require exactly that UUID to appear as one
   `ChangedEntitlement`. Its row is expected to re-stamp/de-download under the
   existing real-change contract; every unchanged control row must remain
   byte-identical. Restore or re-download the disposable control afterward.

Hardware status: **ASSUMED pending manager run**. Phase A, not Layer 2, is the
first decision point. Do not label either layer hardware/end-to-end verified
until its corresponding database comparison has been completed.
