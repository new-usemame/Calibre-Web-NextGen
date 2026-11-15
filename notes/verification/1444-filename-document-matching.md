# KOReader filename document matching (#1444)

## Result and product decisions

Binary remains the default for existing and new devices. Tools → CWNG library → Advanced → Document matching method offers an explicit, persistent Filename choice. Filename uses MD5 of the exact UTF-8 basename, including extension, matching stock KOReader KOSync semantics. It lets converted or metadata-edited sideloaded copies keep a progress/highlight identity when their bytes differ but their library/download name stays the same. Case, accents and extension matter; renamed files do not match, and identical names can collide. The menu help and guide explain these costs.

The choice applies to future progress, annotation and inventory matching. Captured offline queue entries keep their original identity. Changing the setting clears push/pull debounce timestamps and persists immediately. Unknown stored modes fall back to Binary.

Sync identity and file integrity are separate: getDocumentContentDigest retains the existing partial-MD5/cache precedence for delivery verification, managed-file reconciliation and deletion safeguards. Those operations must continue checking contents even when progress matches by name. No server fallback or fuzzy name search was added; the server already registers filename checksums.

## Behavioral evidence

- New whole-plugin Lua matching test first failed because the Advanced menu did not offer the choice. A second regression assertion failed when Filename accidentally changed managed-file integrity checks; it passes after separating identity from content digests.
- Whole-plugin callbacks exercise persisted choices, current/explicit paths, UTF-8 names, case/extension differences, missing names, unknown-mode fallback, zero file reads in Filename mode, real offline queue capture with an old Binary entry preserved beside a new Filename entry, inventory identity and binary managed-file probing.
- Focused final Python wrappers: **22 passed**. All **21 plugin Lua suites passed**. Real KOReader LuaJIT ffi/sha2 runtime passed the matching suite, including MD5 oracle `9ea1b31e133214bb1169acce6ff4affb`.
- Real KOReader 2026.07.1 SDL emulator, with this worktree's plugin mounted: chose Filename, checked persisted settings, quit and restarted, confirmed Filename remained selected, restored Binary and checked persisted settings again. Reviewed compressed screenshots of initial Binary, chosen Filename, restart persistence and restored Binary. No plugin error appeared in the retained runtime log. The emulator used an owned temporary home and was removed after verification.
- Full local smoke/unit run: **10,248 passed, 124 skipped, 2 failed**. Both failures are the existing macOS SQLite I/O failures in `test_2291_replaced_metadata_db_reconnects.py`; there are no other failures. The older source-shape digest tests were replaced with execution of the existing Lua digest/precedence suites, so the refactor keeps behavioral coverage.
- Independent source review initially found the identity/integrity coupling. After the fix, the reviewer reported no unresolved blocker. Root checked the final diff and emulator evidence independently.

## Limits

No physical Kobo/Kindle was touched, and the emulator was not connected to a production server. Emulator settings/menu persistence and real digest computation are observed; physical-device live HTTP progress/highlight exchange is not claimed. Existing server filename-registry contract tests cover the receiving seam. The plugin release version is unchanged for the operator's release train.

## Current-main integration, 2026-10-02

Rebased onto `07c361c2e`, the translation follow-up after #2401. Only the CHANGES table row required resolution; every preceding row and SPA translation anchor is retained. The shipped plugin and its behavioral suites are byte-identical to the originally reviewed `fe8dad0cc` tree. All 21 Lua suites passed again, and the focused plugin/digest/inventory/delivery/server filename-contract packet passed 73 tests with eight existing environment skips. Original emulator persistence/restart and real LuaJIT digest evidence remains applicable; physical-device/live-production sync is not claimed. Fresh exact-head CI is the final merge gate.
