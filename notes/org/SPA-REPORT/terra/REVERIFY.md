# SPA reader Phase 1 — Terra re-verification

## Verdict: PASS

All three prior release blockers are resolved in the current uncommitted fixes. The uncertain SPA-only PageUp/PageDown/Space/touch enhancement is removed; arrow-key navigation and on-screen previous/next controls remain.

No source files were edited and no test suite was run by this verifier, per the recheck-only scope.

## Findings

### F1 — PASS: font range parity

- **OBSERVED:** The canonical sanitizer accepts `fontSize` 75–200 (`cps/reader_settings.py:41-44`).
- **OBSERVED:** Classic is 75–200 (`cps/templates/read.html:116-120`); SPA now declares `FONT_MIN = 75` and `FONT_MAX = 200` (`frontend/src/pages/Reader.tsx:50-51`) and uses those bounds in its range control (`Reader.tsx:572-580`).
- **OBSERVED:** SPA hydration directly uses the server font-size value (`Reader.tsx:212-221`), so all valid canonical values are representable.

### F2 — PASS: classic `lineHeight` is consumed, applied, and controlled

- **OBSERVED:** Classic types `lineHeight` as an integer and posts it through the shared API (`cps/static/js/reading/reader-settings.js:18,49-65`).
- **OBSERVED:** Classic provides a 100–220 line-height control with live readout, restores its value, applies changes, and persists them (`cps/templates/read.html:131-137,326-338`).
- **OBSERVED:** `applyReaderLineHeight` applies a rendition-theme line-height override (`cps/static/js/reading/epub.js:110-121`) and saved `lineHeight` is restored during setup (`epub.js:325-330`). The canonical range is 100–220 (`cps/reader_settings.py:42-44`).

### F3 — PASS: dedicated route ownership

- **OBSERVED:** Classic POSTs only to `/api/v1/reader/settings` (`cps/static/js/reading/reader-settings.js:55-60`).
- **OBSERVED:** The `/ajax/readersettings` handler is removed from `cps/web.py`; no runtime reference or route declaration remains.
- **OBSERVED:** The sole writer merges validated partial updates and commits `User.view_settings` (`cps/api/reader.py:87-107`); the API blueprint prefix makes this `/api/v1/reader/settings` (`cps/api/__init__.py:15`).

### Navigation — PASS

- **OBSERVED:** `Reader.tsx` no longer contains PageUp, PageDown, Space, `TouchEvent`, `touchstart`, `touchend`, or the prior `handleReaderKey` enhancement.
- **OBSERVED:** ArrowLeft/ArrowRight remain bound via document and rendition `keyup` (`Reader.tsx:425-434`); existing on-screen previous/next buttons remain (`Reader.tsx:603-608`).
- **ASSUMED:** The rendition event path receives iframe keyboard events; interactive browser verification was intentionally not rerun.

## Evidence

- **OBSERVED:** Targeted `git diff --check` for the six requested source/test files exited cleanly.
- **NOT RERUN:** Manager-reported 35 focused passing tests and successful frontend build.
