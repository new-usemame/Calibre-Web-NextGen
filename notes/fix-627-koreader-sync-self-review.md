# #627 KOReader visible-progress self-review

## Findings

- ✅ **Verified-strong — reporter semantics.** OBSERVED in the July 14
  screenshots: “entry” is the `KOReader Progress` metadata row. The same book
  already has a `Currently reading` badge, so the badge is not the missing
  object.
- ✅ **Verified-strong — failure-mode classification.** Class 1 (the real
  client's full flow was not followed), reinforced by Class 3 (device-leg
  claims were assumed) and Class 5 (the reporter's pre-existing read-state
  shape was not mirrored). The prior checksum/ingest fixes proved
  book resolution and device transport, but not the separate
  `KoboReadingState.current_bookmark` carrier read by classic and SPA details.
- ✅ **Verified-strong — exact regression.** With the production change
  temporarily reversed, the auth + PUT test stores `KOSyncProgress` and updates
  `ReadBook`, then fails with `NoResultFound` for `KoboReadingState`. The same
  test passes with the change.
- ✅ **Verified-strong — live wire and both consumers.** On isolated
  `cwn-p627:8107`, the v4.1.12 baseline accepted the plugin handshake and PUT
  but left zero Kobo-state rows. After copying the patch into the same
  container and replaying the same sequence, the database held one state,
  bookmark, and statistics row; classic rendered `68.9%`; SPA API returned
  `68.86`.
- ✅ **Verified-strong — concurrency.** Eight parallel device PUTs against a
  legacy `ReadBook` converged on one progress/state/bookmark row and the
  furthest 68.0% position. No lock, uniqueness, or ORM errors appeared.
- ✅ **Verified-strong — client compatibility.** The shipped plugin already
  configures this server through `GET /kosync/users/auth` and sends the same
  `/kosync/syncs/progress` PUT. The observed handshake response remained the
  expected `{"authorized":"OK"}`. This fix changes only server persistence,
  so it is not inert behind a plugin update.
- ✅ **Verified-strong — #906 accounted for.** PR #906 is in the base but not
  the reporter's release. It changes KOReader highlight-deletion reconciliation
  and the device plugin, not progress visibility. Its real-device leg remains
  ASSUMED.
- ✅ **Verified-strong — cluster scope.** #509 contains the same “badge exists,
  no KOReader progress” web-visibility symptom and shares this cause. #633 is a
  different furthest-position/automatic-trigger problem; its percentage
  preservation work remains independent.
- ✅ **Greptile P2 dispositioned.** The reviewer correctly noted that the first
  regression covered a fully missing state but not partial legacy graphs.
  Parametrized behavioral cases now cover missing bookmark, missing statistics,
  and both missing while preserving the existing parent row.

## Risks and disposition

- No 🔴 or 🟠 findings remain.
- The helper creates the same three-row state graph the pre-existing new-book
  branch already created, and both branches now call that one helper. No schema,
  route, dependency, external URL, auth, CSRF, or user-visible string changed.
- Physical KOReader/PocketBook hardware was unavailable. That final client leg
  is ASSUMED; all server-side wire, database, classic, and SPA links are
  OBSERVED.
- The integration selection collected 241 tests: 198 passed and 43 fixture
  setup errors occurred because the globally named `cwa-test-container` belongs
  to another concurrent worktree. The isolated KOReader unit selection is clean
  at 195/195, and the assigned `cwn-p627` live flow is clean. The foreign
  container was not touched. After the review addition, the isolated KOReader
  unit selection is clean at 198/198.
