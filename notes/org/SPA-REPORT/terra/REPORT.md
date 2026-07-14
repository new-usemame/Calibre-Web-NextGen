# SPA reader Phase 1 — adversarial verifier report

## Verdict: FAIL

The appearance-settings hydration path is sound enough to gate reader creation, but the web progress implementation is not safe to share the KOReader KOSync record. It can give KOReader an EPUB CFI as its native locator, lose a correct percentage during initial restore/unload, and accept stale out-of-order writes as deliberate rewinds.

## Required fixes

1. **Do not write an EPUB CFI into `KOSyncProgress.progress`.** That field is returned verbatim by `GET /kosync/syncs/progress/<document>` as KOReader's locator. Keep cross-engine synchronization percentage-only, or retain a KOSync-compatible locator separately and make the web CFI private to `Bookmark`.
2. Make save arbitration atomic and ordered. Validate the complete body before touching either store; use a transaction/constraint or compare-and-set revision so a delayed relocation request cannot overwrite a newer pagehide save merely because it shares `device_id`.
3. Do not mark the UI at `currentPct` without also assigning `lastProgressRef.current`. Defer the first save until locations are ready, or derive the exact CFI+percentage atomically. On location-generation failure, preserve a safe CFI rather than relying on the best-effort percentage jump.
4. Preserve the cross-device winner in the bookmark response/store as well as KOSync. A lower cross-device POST currently replaces `Bookmark.bookmark_key`; restore only self-heals after `locations.generate()` succeeds.
5. Add real DB + HTTP regressions: checksum-keyed KOReader row ↔ web write ↔ KOReader GET must never exchange incompatible locators; malformed/missing paired fields must leave both stores unchanged; initial-display-before-locations then pagehide; reversed completion of relocation/pagehide POSTs; concurrent first writers; same-device rewind and cross-device reject across checksum aliases.

## Findings

### F1 — FAIL: incompatible locator corrupts KOSync semantics

- **OBSERVED:** `cps/api/reader.py:101-114` creates/updates `KOSyncProgress.progress` with `bookmark_key`, which this endpoint documents as an EPUB CFI.
- **OBSERVED:** `cps/progress_syncing/protocols/kosync.py:712-720` returns `progress_record.progress` in the KOSync response; the existing protocol accepts and stores KOReader's xpointer/`cre://` locator (`kosync.py:799-810`, `846-861`).
- **OBSERVED:** the program design explicitly identifies these as distinct grammars and designates percentage as the initial common carrier (`notes/web-reader-DESIGN.md` §1).
- **Impact:** when a web write wins (or is the first row), a KOReader pull receives `epubcfi(...)` as `progress`, not a KOReader position. Conversely, a cross-device KOReader winner has no web CFI; its percent conversion is only a best-effort front-end reconstruction.

### F2 — FAIL: percentage/CFI inconsistency during initial restore

- **OBSERVED:** the first `relocated` event uses `lastProgressRef.current` until locations exist (`Reader.tsx:402-410`); it immediately schedules a save (`249-257`).
- **OBSERVED:** after locations load, the non-forward branch only calls `setProgress(currentPct)` (`383-397`), leaving `lastProgressRef.current` unchanged. Pagehide/unmount sends that ref (`442-452`, `463-471`).
- **Impact:** opening at a saved CFI before the asynchronous location map exists can display (for example) 54% but save that CFI with 0%. It violates the claimed “exact page” flush and can make the next restore treat a valid CFI as 0% progress.

### F3 — FAIL: relocation/unload races can rewind a newer same-device position

- **OBSERVED:** every relocation sends an independent debounced mutation (`Reader.tsx:249-257`); `visibilitychange`, `pagehide`, and cleanup send additional independent keepalive requests (`442-452`, `463-475`). No sequence/revision is sent.
- **OBSERVED:** same-device writes are expressly accepted even when lower (`reader.py:108-115`), matching KOSync's deliberate-rewind rule (`kosync.py:846-856`).
- **Impact:** a stale earlier relocation request that arrives after a later pagehide request is indistinguishable from a deliberate rewind and wins. The save ordering is therefore network-arrival order, not reader order.
- **OBSERVED:** `KOSyncProgress` defines no uniqueness constraint for `(user_id, document)` (`models.py:466-476`), so concurrent first writes can also create multiple rows; `_furthest_progress` then selects by percent/timestamp rather than a single atomic owner.

### F4 — FAIL: invalid progress payloads are not atomic

- **OBSERVED:** validation runs only if all of `bookmark_key`, `percentage`, and `device_id` are truthy (`reader.py:77-86`), while bookmark deletion happens unconditionally afterward (`88-96`). Thus `{bookmark: validCfi, percentage: "bad"}` with no `device_id` deletes/replaces the legacy bookmark instead of returning a validation error; invalid JSON is coerced to `{}` and clears it.
- **OBSERVED:** an array JSON body reaches `data.get` and raises rather than returning the API's structured 400 (`77-80`).
- **ASSUMED:** the blueprint error handler rolls this exception to JSON 500 in the full authenticated app; direct isolated HTTP could not reach the handler without credentials.

### F5 — FAIL: cross-device furthest restore depends on a best-effort second phase

- **OBSERVED:** a lower cross-device save always replaces `ub.Bookmark` before the KOSync comparison (`88-115`), even when the KOSync row remains farther.
- **OBSERVED:** the SPA repairs this only after `locations.generate()` and `cfiFromPercentage()` both work (`383-400`). Failures are silently ignored.
- **Impact:** a location-generation error, malformed book, or close before that promise completes restores the lower CFI despite the farther KOSync percentage. The invariant is not durable at the CFI layer.

### F6 — PASS (limited): checksum/book alias lookup and intended policy match KOSync

- **OBSERVED:** `_furthest_progress` searches the numeric book key plus every registered checksum and orders furthest percentage then timestamp (`reader.py:43-49`), matching `get_progress_record` (`kosync.py:570-585`).
- **OBSERVED:** the KOSync suite pins lower cross-device preservation, same-device rewind, and checksum alias lookup; it passed in this worktree.
- **Limit:** F1 means the matching lookup policy is applied to incompatible locators, so this is not an end-to-end pass.

### F7 — PASS (limited): settings hydration does not start the rendition with the default snapshot

- **OBSERVED:** the reader waits for bookmark fetch, settings fetch, and `settingsHydrated`; hydration sets all appearance state before enabling initialization (`Reader.tsx:222-233`, `313-316`). This avoids a settings-triggered rebuild during restore.
- **OBSERVED:** legacy and SPA settings now share sanitization/partial merge (`cps/reader_settings.py`; `cps/web.py:289-304`; `cps/api/reader.py:134-155`).
- **Limit:** no authenticated browser run was available, so rendered iframe behavior remains **ASSUMED**.

## Verification evidence

- **OBSERVED:** `pytest -q tests/unit/test_api_v1_reader.py tests/unit/test_633_kosync_furthest_position.py tests/unit/test_kosync_book_id_keyed_lookup.py tests/unit/test_633_kosync_cross_device_orphan.py tests/unit/test_reader_settings_persist.py` — **39 passed**.
- **OBSERVED:** `git diff --check` passed with no output.
- **OBSERVED:** `GET http://localhost:8101/api/v1/health` returned `200 {"api":"v1","status":"ok"}`. Unauthenticated POSTs were stopped by the server's pre-route gate, so no authenticated HTTP/database mutation was performed.
- **OBSERVED:** the new Playwright spec could not run: its configured `http://localhost:8086/app` refused connection during global setup. This does not validate progress or modality.
- **OBSERVED:** the existing/new tests cover simple mocked API branches and a single-device reload, but contain no malformed bookmark-body, KOSync-locator compatibility, checksum-backed SPA write, first-relocation-before-locations, pagehide ordering, concurrent-writer, or cross-device browser E2E case.
