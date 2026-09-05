**OBSERVED — #324 inbound reader resume, local verification on 2026-09-05.**

The implementation was developed on `feat/324-resume-from-koreader-position`, starting at `679ac40cf272d4f143ed98600c5653b652e04e97`. All work and protocol replay were local. The final commit SHA is supplied in the handoff; this report is committed with the implementation.

**OBSERVED — behavior and code evidence.**

| Behavior | Code evidence |
| --- | --- |
| The authenticated bookmark GET returns the existing CFI plus an optional percentage resume hint. Authentication still runs first. | `cps/api/reader.py:29`, `cps/api/reader.py:44`, `cps/api/reader.py:52` |
| Reads use a separate read-only SQLite connection, zero busy timeout, and a transaction snapshot. A missing optional carrier preserves an already-read CFI; an inaccessible database returns an empty successful hint. No ORM flush, commit, UPDATE, or DELETE occurs in this read path. | `cps/services/reading_position.py:262`, `:279`, `:282`, `:288`, `:311` |
| Percentages must be finite and in 0–100. Remote timestamps are normalized to UTC. Equal, older, and unknown local clocks preserve the local CFI. A known newer remote yields an offer; no local CFI yields automatic resume. | `cps/services/reading_position.py:70`, `:299`, `:302`, `:306`, `:308` |
| Bookmark timestamps are nullable; migration preserves existing CFIs and leaves their unknown historical timestamps null. Both save routes stamp after sharing so the browser's own mirror cannot appear newer. | `cps/ub.py:750`, `:4836`, `:4927`; `cps/api/reader.py:114`; `cps/web.py:353` |
| Automatic resume generates locations once, converts the percentage to a CFI, and places it again after typography's layout frame. This last placement is necessary: a measured first attempt showed the target outside the visible spread after reflow. | `frontend/src/pages/Reader.tsx:1045`, `:1054`, `:1063`; `frontend/src/lib/readerResume.ts:7` |
| Initial layout events and accepting the remote offer are previews, so they do not overwrite the local bookmark. Page turns and explicit chapter choices end that suppression and save normally. An offer is also suppressed if reading has already moved while locations were generating. | `frontend/src/pages/Reader.tsx:1052`, `:1086`, `:1113`, `:981`, `:1222`, `:1348` |
| The affordance is a dismissible pair of native buttons, with keyboard focus styling and a width bounded by the viewport. | `frontend/src/pages/Reader.tsx:1348`; `frontend/src/pages/Reader.module.css:610` |

**OBSERVED — units and freshness were traced through both producer protocols.**

| Producer / consumer | Units | Clock used for shared-position freshness | Code evidence |
| --- | --- | --- | --- |
| Browser outbound | epub.js fraction × 100; service receives and stores 0–100 | Server UTC through the shared bookmark writer | `frontend/src/pages/Reader.tsx:1105`; `cps/services/reading_position.py:70`, `:229`, `:240`; `cps/progress_syncing/protocols/kosync.py:586`; `cps/services/device_reading_position.py:218` |
| KOReader PUT | Fraction ≤ 1 × 100 before storing and mirroring; the replay sends `0.375` | Server UTC at receipt / mirror | `cps/progress_syncing/protocols/kosync.py:2010`, `:2021`, `:2124`; `cps/services/device_reading_position.py:218` |
| Kobo PUT | `ProgressPercent` is passed through as 0–100, without fraction scaling | Parsed `ReadingStates[0].LastModified`, supplied by the device | `cps/kobo.py:3646`, `:3661`, `:3688`; serialization at `:4157`, `:4160` |
| Existing display resolution | `KoboBookmark.progress_percent` | That bookmark's `last_modified`, not its parent's status/statistics clock | `cps/helper.py:895` |
| New SPA inbound | API 0–100 divided by 100 for `cfiFromPercentage` | Server decides whether to offer; frontend consumes that decision | `frontend/src/lib/readerResume.ts:12`; `cps/services/reading_position.py:306` |

**ASSUMED — physical clock accuracy and exact cross-engine word equivalence are not established.** Kobo supplies a device clock, whereas KOReader uses server receipt time. The portable coordinate is a percentage; this change does not translate KOReader xpointers or Kobo locators into exact epub.js word anchors.

**OBSERVED — end-to-end boundary.** The test sends a KOReader-style HTTP PUT through the real KOSync handler, persists the real SQLAlchemy/SQLite carriers, reads through the real bookmark GET, and drives the actual React Reader and epub.js in Chrome. It checks automatic resume, the actual visible percentage interval, CFI preservation, keyboard acceptance, dismissal, subsequent browser freshness, a 390 px viewport, and chapter navigation. It also verifies only one locations generation for automatic resume. See `tests/integration/test_reader_resume_browser.py:45` and `frontend/e2e/reader-resume/run.mjs:20`.

**ASSUMED / deliberately bounded —** authentication, CSRF issuance, Calibre checksum/book metadata lookup, device registration, and Hardcover delivery are fixture boundaries (`tests/integration/test_reader_resume_browser.py:33`). The test mounts the real Reader in its normal query/i18n/announcer providers, rather than running the entire SPA shell. No physical KOReader/Kobo, production login session, Docker deployment, or household library was exercised. The locked-database timing assertion establishes the bound of the position lookup; it does not establish a bound on the application's pre-existing authenticated user loader when the entire app database is exclusively locked.

**OBSERVED — commands and red/green evidence.** Commands below ran from the worktree root unless prefixed with `cd frontend`. Output blocks are real excerpts, with unrelated collection warnings omitted. Full logs remain in the session's `/tmp/324-*.log` files. Existing installed packages were reused; no dependency was added.

For red evidence, the test files stayed in place while every changed product file under `cps` and `frontend/src` was restored from the base commit. The new `readerResume.ts` was absent, as it is on that base. Each product file was then restored from the local checkpoint, followed by a clean status check. The orchestration command was:

```sh
python3 /tmp/324-probe.py baseline > /tmp/324-baseline-final-summary.log 2>&1
```

Its actual test commands were:

```sh
CWNG_PYTEST_TMP_BASE=/tmp/cwng-324-tests PYTHONDONTWRITEBYTECODE=1 \
  /tmp/kobo-budget-venv/bin/python -m pytest \
  tests/unit/test_reader_resume.py tests/integration/test_reader_resume_browser.py -q -s
cd frontend
node --experimental-strip-types --test unit/readerResume.test.ts
```

```text
Seek failed: expected 37.5 observed 0 bookmark API {"bookmark":"epubcfi(/6/2!/4/1:0)"}
FAILED tests/unit/test_reader_resume.py::test_remote_resume_is_scoped_read_only_and_freshness_ordered
FAILED tests/unit/test_reader_resume.py::test_unusable_or_unavailable_progress_retains_cfi_with_bounded_lock_wait
FAILED tests/unit/test_reader_resume.py::test_migration_preserves_unknown_old_cfi_and_is_repeatable
FAILED tests/unit/test_reader_resume.py::test_both_browser_writes_win_over_their_own_mirror_even_without_percentage
FAILED tests/integration/test_reader_resume_browser.py::test_koreader_http_to_real_spa_epub_resume
============================== 5 failed in 43.29s ==============================
```

The Node red run failed with `ERR_MODULE_NOT_FOUND` for the new adapter: `tests 1`, `pass 0`, `fail 1`. The additive backend tests likewise encounter unavailable new schema/helper behavior on the base. Those failures alone do not prove the arithmetic or ordering assertions; the behavioral mutations below provide that evidence. The browser red run directly reproduces the reported 0% symptom after a successful 37.5% sync.

Final green repeats used this exact loop, without retry-to-green:

```sh
for run in 1 2; do
  CWNG_PYTEST_TMP_BASE=/tmp/cwng-324-tests /tmp/kobo-budget-venv/bin/python -m pytest \
    tests/unit/test_reader_resume.py tests/integration/test_reader_resume_browser.py -q -s \
    > "/tmp/324-verified-green-$run.log" 2>&1 || exit 1
done
```

```text
run 1: ============================== 5 passed in 46.48s ==============================
run 2: ============================== 5 passed in 36.15s ==============================
```

Both runs printed:

```text
Visible percentage range: [ 37.2093023255814, 38.759689922480625 ]
Newer remote -> local retained -> keyboard resume at 71 % -> CFI unchanged
Dismiss + browser page turn -> newer local suppresses stale 72%
390px notice fits; chapter selection persists and removes the remote offer
```

**OBSERVED — measured EPUB cost.** `stat -f '%z bytes' tests/fixtures/sample_books/christmas_carol.epub` returned `642727 bytes`. On this real EPUB, the two final runs measured `locations.generate(1600)` at `2290.900000002235` ms and `2248.39999999851` ms, once per automatic opening. Navigation-to-visible-position time was 4282 ms and 3634 ms, respectively. The readout was 37%; the measured visible range above contains 37.5%. Local-bookmark openings do not wait for this index before displaying their CFI. No extrapolation to larger EPUBs or slower hardware is claimed.

**OBSERVED — mutation probes.** The orchestration commands were:

```sh
python3 /tmp/324-probe.py freshness > /tmp/324-freshness-summary.log 2>&1
python3 /tmp/324-probe.py percentage > /tmp/324-percentage-summary.log 2>&1
python3 /tmp/324-probe.py fraction > /tmp/324-fraction-summary.log 2>&1
python3 /tmp/324-probe.py unknown-clock > /tmp/324-unknown-clock-summary.log 2>&1
python3 /tmp/324-stamp-probe.py > /tmp/324-stamp-summary.log 2>&1
```

The backend probes execute `/tmp/kobo-budget-venv/bin/python -m pytest tests/unit/test_reader_resume.py -q` with `CWNG_PYTEST_TMP_BASE=/tmp/cwng-324-tests`; the first four also set `PYTHONDONTWRITEBYTECODE=1`. The frontend fraction probe executes `node --experimental-strip-types --test unit/readerResume.test.ts` in `frontend`, followed by `/tmp/kobo-budget-venv/bin/python -m pytest tests/integration/test_reader_resume_browser.py -q -s` with those environment variables.

| Mutation | Tests that went red | Actual result |
| --- | --- | --- |
| Replace `synced_at <= utc(local[1])` with `>=` | `test_remote_resume_is_scoped_read_only_and_freshness_ordered`; `test_both_browser_writes_win_over_their_own_mirror_even_without_percentage` | `2 failed, 2 passed in 6.76s` |
| Divide the validated backend percentage by 100 before returning it | `test_remote_resume_is_scoped_read_only_and_freshness_ordered` | `1 failed, 3 passed in 6.48s` |
| Remove `/ 100` from `resumeCfi` | The portable-percentage Node test and `test_koreader_http_to_real_spa_epub_resume` | Node: `pass 0`, `fail 1`; browser: `1 failed in 45.36s` |
| Remove the `not local[1] or` guard | None | `4 passed in 7.15s` |
| Move the SPA bookmark timestamp assignment before progress sharing | `test_both_browser_writes_win_over_their_own_mirror_even_without_percentage` | `1 failed, 3 passed in 6.73s` |

The frontend mutant's browser output was:

```text
Seek failed: expected 37.5 observed 100 bookmark API {"bookmark":null,"resume":{"mode":"automatic","percentage":37.5,"synced_at":"2026-09-05T09:21:36.568653+00:00"}}
```

The unknown-clock survivor is defense in depth: the freshness test actually supplies a null historical local clock. Removing the explicit guard causes timestamp parsing to raise inside the defensive boundary, which returns the already-read local CFI without a hint. The same user-visible invariant therefore still holds; this is not a missing null-clock test.

After every mutation restoration, and both base-tree restorations, the probe executed:

```sh
git restore --source=HEAD -- <the modified product paths>
git status --porcelain
```

Every status output was empty. Mutations were performed against a committed local checkpoint so this check covered the entire worktree, not merely the mutated file.

**OBSERVED — frontend and adjacent validation.**

```sh
cd frontend
node node_modules/typescript/bin/tsc -p tsconfig.e2e.json --noEmit > /tmp/324-verified-typecheck.log 2>&1 &&
node --experimental-strip-types --test unit/*.test.ts tests/unit/readerTarget.test.ts > /tmp/324-verified-frontend-unit.log 2>&1 &&
node --experimental-strip-types --test unit/readerResume.test.ts > /tmp/324-verified-node-repeat.log 2>&1 &&
node node_modules/typescript/bin/tsc -b &&
node node_modules/vite/bin/vite.js build > /tmp/324-verified-build.log 2>&1
```

```text
Typecheck: exit 0, no output
Frontend suite: tests 61; pass 61; fail 0
Separate new-test repeat: tests 1; pass 1; fail 0
vite v5.4.21 building for production...
✓ 1938 modules transformed.
✓ built in 10.56s
```

The build also emitted its existing advisory about chunks larger than 500 kB. The new TypeScript test thus passed both within the suite and in a separate invocation.

```sh
CWNG_PYTEST_TMP_BASE=/tmp/cwng-324-tests /tmp/kobo-budget-venv/bin/python -m pytest \
  tests/unit/test_reader_resume.py tests/unit/test_api_v1_reader.py \
  tests/unit/test_324_web_reader_progress_writeback.py tests/unit/test_1366_web_reader_to_koreader.py \
  tests/unit/test_1942_device_reading_position.py tests/unit/test_bookmark_format_sync.py \
  tests/unit/test_migrate_bookmark_format_lowercase.py tests/unit/test_kobo_bookmark_created_at.py \
  tests/unit/test_f6f9187_kosync_bookmark_mirror_arbitration.py tests/unit/test_translations_compile.py \
  -q > /tmp/324-verified-adjacent.log 2>&1
```

```text
================= 142 passed, 1 skipped, 2 warnings in 32.34s ==================
```

The skip is the existing CI-only gettext availability assertion, which explicitly skips when `CI` is unset (`tests/unit/test_translations_compile.py:81`). The actual locale compilation tests ran. The two changed catalogs also passed `msgfmt --check --output-file=/tmp/324-de.mo cps/translations/de/LC_MESSAGES/messages.po` and the analogous `fr` command, both with exit 0 and no output. `python3 scripts/extract_spa_strings.py --write` reported `[spa_strings] wrote AUTOGEN block: 824 anchored msgid(s)`.

**OBSERVED — decisions and exclusions.**

- Legacy inbound UI was left unchanged: it already reads a fallback synced percentage (`cps/web.py:3763`), and this objective is the SPA reader. Its save timestamp was updated because both readers own the same CFI row.
- No historical timestamps were invented. Old local bookmarks with unknown age keep their local position until a subsequent browser save gives them a real clock. Their relative age cannot be recovered from the old schema. This is a conservative exception to offering on every remote/local pair, explicitly chosen to protect existing bookmarks.
- No xpointers/CFIs were translated across engines; only the portable percentage is consumed.
- No production or household stack was rebuilt or restarted. No physical-device, Safari/WebKit, screen-reader, or authenticated full-database-lock result is claimed. Chrome keyboard and narrow-viewport behavior were exercised locally.
- No new dependency, license change, external service URL, push, PR, merge, release, or GitHub message was introduced. The protected harness files were not edited. A scoped `git diff --name-only 679ac40cf272d4f143ed98600c5653b652e04e97 HEAD -- scripts/autopilot-tick.sh scripts/lib scripts/preflight.sh tests/autopilot frontend/package.json frontend/package-lock.json LICENSE` returned no output.
- Two old bookmark-GET tests that mocked the ORM query chain were replaced by real SQLite and HTTP/browser coverage; the unchanged anonymous-authentication test remains. No source-text pin was added.
- Creating the requested external scratch directory stalled and was stopped. No installation completed there. Existing Python/Node tooling was reused, and the temporary frontend package symlink and task-owned temporary database directories were removed at the end. Small evidence logs were retained locally.

**OBSERVED — limits of the lock fallback.** Optional-table failure retains the local CFI. An exclusive lock that also prevents reading the local bookmark leaves no safe CFI to return; the lookup returns `{bookmark: null, resume: null}` promptly. This bounds the additional position lookup and prevents its exceptions from becoming a 500. Existing authentication/session database access remains outside this change and outside the measured lock bound.
