**OBSERVED — #324 inbound reader resume, local verification on 2026-09-05; corrected after review of `ac20f830d5`.**

The implementation was developed on `feat/324-resume-from-koreader-position`, starting at `679ac40cf272d4f143ed98600c5653b652e04e97`. All work and protocol replay were local. The final commit SHA is supplied in the handoff; this report is committed with the implementation.

**OBSERVED — behavior and code evidence.**

| Behavior | Code evidence |
| --- | --- |
| The authenticated bookmark GET returns the existing CFI plus an optional percentage resume hint. Authentication still runs first. | `cps/api/reader.py:29`, `cps/api/reader.py:44`, `cps/api/reader.py:52` |
| The mandatory CFI lookup uses the app session and its normal busy timeout, with autoflush disabled. Only optional progress uses a separate read-only SQLite connection with zero busy timeout. Optional failure preserves the CFI; mandatory lookup errors propagate instead of falsely reporting absence. No flush or write occurs in this read path. | `cps/services/reading_position.py:273`, `:281`, `:283`, `:288`, `:312` |
| Percentages must be finite and in 0–100. Remote timestamps are normalized to UTC. Equal or older remote clocks yield no hint. A newer remote or unknown local clock yields an offer while preserving the local CFI; no local CFI yields automatic resume. | `cps/services/reading_position.py:70`, `:300`, `:303`, `:307`, `:309` |
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
| New SPA inbound | API 0–100 divided by 100 for `cfiFromPercentage` | Server decides whether to offer; frontend consumes that decision | `frontend/src/lib/readerResume.ts:12`; `cps/services/reading_position.py:307` |

**ASSUMED — physical clock accuracy and exact cross-engine word equivalence are not established.** Kobo supplies a device clock, whereas KOReader uses server receipt time. The portable coordinate is a percentage; this change does not translate KOReader xpointers or Kobo locators into exact epub.js word anchors.

**OBSERVED — end-to-end boundary.** The test sends a KOReader-style HTTP PUT through the real KOSync handler, persists the real SQLAlchemy/SQLite carriers, reads through the real bookmark GET, and drives the actual React Reader and epub.js in Chrome. It checks automatic resume, the actual visible percentage interval, CFI preservation, keyboard acceptance, dismissal, subsequent browser freshness, a 390 px viewport, and chapter navigation. It also verifies only one locations generation for automatic resume. See `tests/integration/test_reader_resume_browser.py:45` and `frontend/e2e/reader-resume/run.mjs:20`.

**ASSUMED / deliberately bounded —** authentication, CSRF issuance, Calibre checksum/book metadata lookup, device registration, and Hardcover delivery are fixture boundaries (`tests/integration/test_reader_resume_browser.py:33`). The test mounts the real Reader in its normal query/i18n/announcer providers, rather than running the entire SPA shell. No physical KOReader/Kobo, production login session, Docker deployment, or household library was exercised. The corrected locked-database timing assertion bounds only the optional lookup after the CFI has been read. The mandatory local read and pre-existing authenticated user loader retain their app-connection timeout behavior.

**OBSERVED — historical first-leg commands and red/green evidence (before the correction below).** Commands below ran from the worktree root unless prefixed with `cd frontend`. Output blocks are real excerpts, with unrelated collection warnings omitted. Full logs remain in the session's `/tmp/324-*.log` files. Existing installed packages were reused; no dependency was added.

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

OBSERVED — historical interpretation corrected: the unknown-clock survivor preserved the first leg's no-hint behavior, but that behavior excluded existing users on day one. The correction requires an offer for a null clock and explicitly tests it. The correction probes below supersede this old policy and its survivor interpretation.

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
- No historical timestamps are invented. Old local bookmarks with unknown age keep their local CFI and receive a dismissible offer for valid synced progress. Their relative age cannot be recovered from the old schema, so automatic replacement is inappropriate.
- No xpointers/CFIs were translated across engines; only the portable percentage is consumed.
- No production or household stack was rebuilt or restarted. No physical-device, Safari/WebKit, screen-reader, or authenticated full-database-lock result is claimed. Chrome keyboard and narrow-viewport behavior were exercised locally.
- No new dependency, license change, external service URL, push, PR, merge, release, or GitHub message was introduced. The protected harness files were not edited. A scoped `git diff --name-only 679ac40cf272d4f143ed98600c5653b652e04e97 HEAD -- scripts/autopilot-tick.sh scripts/lib scripts/preflight.sh tests/autopilot frontend/package.json frontend/package-lock.json LICENSE` returned no output.
- The first leg removed two mocked bookmark-GET tests. The correction restores both named endpoint contracts through Flask HTTP routing with real SQLite rows. The anonymous-authentication test remains. No source-text pin was added.
- Creating the requested external scratch directory stalled and was stopped. No installation completed there. Existing Python/Node tooling was reused, and the temporary frontend package symlink and task-owned temporary database directories were removed at the end. Small evidence logs were retained locally.

**OBSERVED — corrected lock availability boundary.** The required local lookup uses `ub.session`, as the pre-feature GET did. It can wait out a transient writer; the correction test holds an exclusive lock for 200 ms. A separate case acquires an exclusive lock after the local lookup and holds it throughout the optional read: the response retains the local CFI and returns within 500 ms. A lock that outlasts the app connection's busy timeout can still fail the mandatory lookup, as before the feature; it is not converted into a successful null bookmark. No availability beyond the original SQLite/app-session behavior is claimed.

**OBSERVED — correction red / green / green-again.** The final corrected tests were run against the parent service from `ac20f830d5` twice, then against the corrected service twice. Only `cps/services/reading_position.py` was switched; the route implementation is unchanged. The exact commands were:

```sh
python3 - <<'PY'
from pathlib import Path
import subprocess
p = Path('cps/services/reading_position.py')
Path('/tmp/324-correction-service.py').write_bytes(p.read_bytes())
p.write_bytes(subprocess.check_output(['git', 'show', 'ac20f830d5:cps/services/reading_position.py']))
PY
for run in 1 2; do
  CWNG_PYTEST_TMP_BASE=/tmp/cwng-324-correction-tests PYTHONDONTWRITEBYTECODE=1 /tmp/kobo-budget-venv/bin/python -m pytest tests/unit/test_reader_resume.py tests/unit/test_api_v1_reader.py -q > "/tmp/324-correction-final-red-$run.log" 2>&1
  tail -1 "/tmp/324-correction-final-red-$run.log"
done
cp /tmp/324-correction-service.py cps/services/reading_position.py
for run in 1 2; do
  CWNG_PYTEST_TMP_BASE=/tmp/cwng-324-correction-tests PYTHONDONTWRITEBYTECODE=1 /tmp/kobo-budget-venv/bin/python -m pytest tests/unit/test_reader_resume.py tests/unit/test_api_v1_reader.py -q > "/tmp/324-correction-final-green-$run.log" 2>&1 || exit 1
  tail -1 "/tmp/324-correction-final-green-$run.log"
done
```

```text
========================= 4 failed, 10 passed in 8.67s =========================
========================= 4 failed, 10 passed in 8.57s =========================
============================== 14 passed in 8.83s ==============================
============================== 14 passed in 8.84s ==============================
```

OBSERVED — both red runs named these failures:

```text
FAILED tests/unit/test_reader_resume.py::test_unusable_or_unavailable_progress_retains_cfi_with_bounded_lock_wait
FAILED tests/unit/test_reader_resume.py::test_local_cfi_waits_out_transient_app_database_writer
FAILED tests/unit/test_reader_resume.py::test_unknown_local_clock_offers_remote_without_changing_saved_cfi
FAILED tests/unit/test_api_v1_reader.py::test_get_bookmark_returns_key
```

OBSERVED — the first two failed because `bookmark` was null rather than `local-cfi` (deliverable 1). The route test failed because its actual HTTP response lost `epubcfi(/6/8)` when the app used in-memory SQLite (deliverable 2). The unknown-clock test failed because `resume` was null instead of an offer containing 37.5 and the server timestamp (deliverable 3). `test_get_bookmark_none_when_absent` passed on the parent and both green runs: its response contract was already correct, and the restored test protects user/book/format scoping. This baseline pass is not represented as regression detection.

OBSERVED — independent read-only review found no blocking implementation issue. Its two test findings were addressed before these final runs: the optional-lock case now restores a valid carrier after testing missing-table fallback, and the transient writer test waits for the release event to avoid a scheduler race after SQLite unlocks.

**OBSERVED — correction mutation probes.** A local checkpoint was committed before mutation, then restored after each probe. The report is amended into that same correction commit afterward, leaving one new commit on the branch. These commands ran:

```sh
for mutant in freshness unknown-clock datetime percentage optional-timeout; do
  python3 /tmp/324-correction-probe.py "$mutant" || exit 1
done
```

OBSERVED — the probe's exact script was:

```python
from pathlib import Path
import os, subprocess, sys
p = Path('cps/services/reading_position.py')
original = p.read_text()
mutants = {
    'freshness': ('synced_at <= utc(local_updated_at)', 'synced_at >= utc(local_updated_at)'),
    'unknown-clock': ('bookmark and local_updated_at is not None and synced_at <= utc(local_updated_at)', 'bookmark and (local_updated_at is None or synced_at <= utc(local_updated_at))'),
    'datetime': ('raw if isinstance(raw, datetime) else datetime.fromisoformat(raw)', 'datetime.fromisoformat(raw)'),
    'percentage': ('"percentage": percentage,', '"percentage": percentage / 100,'),
    'optional-timeout': ('uri=True, timeout=0,', 'uri=True, timeout=5,'),
}
name = sys.argv[1]
a, b = mutants[name]
assert original.count(a) == 1
assert not subprocess.check_output(['git', 'status', '--porcelain'])
try:
    p.write_text(original.replace(a, b))
    cmd = ['/tmp/kobo-budget-venv/bin/python', '-m', 'pytest', 'tests/unit/test_reader_resume.py', 'tests/unit/test_api_v1_reader.py', '-q']
    env = dict(os.environ, CWNG_PYTEST_TMP_BASE='/tmp/cwng-324-correction-tests', PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run(cmd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    Path('/tmp/324-correction-mutant-' + name + '.log').write_text(result.stdout)
    print(name, 'exit', result.returncode)
    print('\n'.join(line for line in result.stdout.splitlines() if line.startswith('FAILED ') or ' failed,' in line or ' passed in ' in line))
finally:
    subprocess.run(['git', 'restore', '--source=HEAD', '--', str(p)], check=True)
    status = subprocess.check_output(['git', 'status', '--porcelain'], text=True)
    print('git status --porcelain:', repr(status))
    assert not status
```

OBSERVED — real output:

```text
freshness exit 1
FAILED tests/unit/test_reader_resume.py::test_remote_resume_is_scoped_read_only_and_freshness_ordered
FAILED tests/unit/test_reader_resume.py::test_local_cfi_waits_out_transient_app_database_writer
FAILED tests/unit/test_reader_resume.py::test_both_browser_writes_win_over_their_own_mirror_even_without_percentage
========================= 3 failed, 11 passed in 8.59s =========================
git status --porcelain: ''
unknown-clock exit 1
FAILED tests/unit/test_reader_resume.py::test_unknown_local_clock_offers_remote_without_changing_saved_cfi
========================= 1 failed, 13 passed in 8.52s =========================
git status --porcelain: ''
datetime exit 1
FAILED tests/unit/test_reader_resume.py::test_remote_resume_is_scoped_read_only_and_freshness_ordered
========================= 1 failed, 13 passed in 8.75s =========================
git status --porcelain: ''
percentage exit 1
FAILED tests/unit/test_reader_resume.py::test_remote_resume_is_scoped_read_only_and_freshness_ordered
FAILED tests/unit/test_reader_resume.py::test_unknown_local_clock_offers_remote_without_changing_saved_cfi
========================= 2 failed, 12 passed in 8.74s =========================
git status --porcelain: ''
optional-timeout exit 1
FAILED tests/unit/test_reader_resume.py::test_unusable_or_unavailable_progress_retains_cfi_with_bounded_lock_wait
======================== 1 failed, 13 passed in 25.00s =========================
git status --porcelain: ''
```

OBSERVED — all five mutants were detected. Reversing freshness breaks both older/newer ordering and browser-save precedence. Reinstating the null-clock suppression specifically fails the day-one offer test. Removing datetime handling keeps the CFI but wrongly drops a valid newer hint, detected by the ordering test. Dividing the wire percentage by 100 fails both automatic and unknown-clock offer expectations. Increasing the optional timeout preserves the CFI but violates the 500 ms contention bound. There is no correction-leg survivor; the first leg's null-clock survivor and its now-obsolete interpretation are retained above as historical evidence.

OBSERVED — availability references: `cps/services/reading_position.py:273` restores the required session lookup outside the optional handler (`:283`, `:312`). `cps/ub.py:5167` configures the production app engine with a 30-second timeout; `git show 679ac40cf2:cps/ub.py` shows the same setting at line 5152. The test fixture uses SQLite's default timeout (`tests/unit/test_reader_resume.py:20`). Thus the restored guarantee is the app session's configured availability, not a newly imposed five-second wait. The exclusive-writer and optional-lock assertions are at `tests/unit/test_reader_resume.py:106` and `:67`; the day-one assertion and unchanged persisted row are at `:130`; restored HTTP contracts are at `tests/unit/test_api_v1_reader.py:59` and `:70`.

**OBSERVED — correction adjacent/backend/browser validation.** Exact command:

```sh
CWNG_PYTEST_TMP_BASE=/tmp/cwng-324-correction-tests PYTHONDONTWRITEBYTECODE=1 /tmp/kobo-budget-venv/bin/python -m pytest tests/unit/test_reader_resume.py tests/unit/test_api_v1_reader.py tests/unit/test_324_web_reader_progress_writeback.py tests/unit/test_1366_web_reader_to_koreader.py tests/unit/test_1942_device_reading_position.py tests/unit/test_bookmark_format_sync.py tests/unit/test_migrate_bookmark_format_lowercase.py tests/unit/test_kobo_bookmark_created_at.py tests/unit/test_f6f9187_kosync_bookmark_mirror_arbitration.py tests/unit/test_translations_compile.py tests/integration/test_reader_resume_browser.py -q -s > /tmp/324-correction-adjacent.log 2>&1
```

OBSERVED — real output excerpts:

```text
Visible percentage range: [ 37.2093023255814, 38.759689922480625 ]
locations.generate(1600) ms: [ 3366.5 ]
KOReader PUT .375 -> API {"bookmark":null,"resume":{"mode":"automatic","percentage":37.5,"synced_at":"2026-09-05T11:08:23.486961+00:00"}} -> rendered 37 % in 4913 ms
Newer remote -> local retained -> keyboard resume at 71 % -> CFI unchanged
Dismiss + browser page turn -> newer local suppresses stale 72%
390px notice fits; chapter selection persists and removes the remote offer
============ 147 passed, 1 skipped, 2 warnings in 97.45s (0:01:37) =============
```

OBSERVED — the same CI-only gettext availability check was skipped; actual locale compilation ran. This correction did not change browser fixtures or frontend source. Day-one null-clock behavior is covered in the service test; existing browser coverage exercises the resulting `offer` mode's acceptance/dismissal behavior, rather than directly creating a null-clock row in the browser fixture.

**OBSERVED — correction frontend validation.** Existing installed node modules were linked temporarily; no installation or dependency change was needed. Exact command, run from `frontend`:

```sh
node node_modules/typescript/bin/tsc -p tsconfig.e2e.json --noEmit > /tmp/324-correction-typecheck.log 2>&1 && node --experimental-strip-types --test unit/*.test.ts tests/unit/readerTarget.test.ts > /tmp/324-correction-frontend-unit.log 2>&1 && node node_modules/typescript/bin/tsc -b >> /tmp/324-correction-typecheck.log 2>&1 && node node_modules/vite/bin/vite.js build > /tmp/324-correction-build.log 2>&1
```

OBSERVED — exit 0. Both typecheck invocations produced no output. Real unit/build output excerpts:

```text
ℹ tests 61
ℹ suites 3
ℹ pass 61
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 2309.947875
✓ built in 12.56s
```

OBSERVED — Vite emitted its existing chunk-size advisory. No frontend implementation changed, so no additional frontend mutation was introduced in this correction; the wire percentage was re-probed at the backend resolution point and the existing fraction adapter ran in the unit suite and real EPUB replay.

**OBSERVED — correction exclusions and cleanup.** No API shape change, historical timestamp backfill, legacy reader change, schema change, dependency installation, requirements edit, license change, or external service URL was needed. Persistent failure of the required app-database read remains the pre-feature error behavior; no cache or fabricated CFI was introduced. No physical-device or production-stack testing was attempted: the local protocol/browser replay supplies the integration evidence. No harness edit, push, PR, GitHub comment, or merge was performed.

OBSERVED — this command returned no output:

```sh
git diff --name-only ac20f830d5 HEAD -- scripts/autopilot-tick.sh scripts/lib scripts/preflight.sh tests/autopilot 'requirements*.txt' frontend/package.json frontend/package-lock.json LICENSE
```

OBSERVED — task database scratch was monitored and cleared before broader validation (42 MB at the last pre-cleanup measurement). Existing installed tools were reused; no large scratch allocation was needed. The task-owned temporary database directory, temporary frontend symlink, generated app build, and small correction scripts/logs were removed after recording the evidence here. Final SHA and clean status are supplied in the handoff because the report is part of that commit.
