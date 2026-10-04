# Book-source edition identity verification

## Behavior and scope

A selected book must remain available after acquisition. Calibre title/author matches identify candidates, not editions: an existing record with missing language can match a different-language source. Previously, a same-format match retained the old file even when the selected artifact contained different bytes.

Acquisition now retains the first deterministic metadata candidate with byte-identical prepared content. Other artifacts create a separate record through the existing Calibre import path, regardless of ordinary duplicate-overwrite settings. Source hashes describe the downloaded artifact; imported hashes describe the prepared file actually stored. Existing book files, annotations and reading positions stay on their original record.

New private results carry artifact-identity policy version 1. Both processor and helper recovery reinspect older metadata-only retention results; legacy imported results still recover normally. Historical completed jobs and application receipts are not replayed or rewritten. Existing request deduplication remains in place; this is a correction for new imports and recoverable in-progress jobs. Resolved format paths must remain inside Calibre's actual library root, independently of any split metadata-database location, for retention, recovery and new import success.

No schema, grant, connection-ownership, UI-string or protocol change. No Anna or per-user connections. Multi-book selection is covered by its separate verification record. The later [EPUB packaging identity slice](virtual-library-epub-repackaging.md) adds a bounded exact-resource fallback after this byte-identical policy; XML/text semantic equivalence remains deferred.

## Observed regression and independent checks

- The original helper/processor behavior failed six owning edition/recovery cases. A real Calibre 9.11 image reproduced selected German bytes being discarded when the existing English record had no language. Explicit English versus German imported separately as the control.
- Owning helper/ingest checks pass 42 cases. They cover distinct and identical artifacts, deterministic selection of a later exact candidate, global-overwrite isolation, deletion/replacement recovery, unverified legacy retention in both recovery entry points, and receipt/membership transaction behavior.
- An independent fresh-context refuter found that an identical outside-library format symlink could be retained by the helper but rejected by receipt verification, creating a retry loop. Fresh-candidate and persisted-result regressions were seen red, then green after aligning resolved containment. The same refuter replayed both cases and cleared the finding; 42 focused cases passed.
- Conditional security review inspected the nonempty product diff and adjacent recovery callers. It found no high-confidence vulnerability. Its static review does not replace the actual runtime checks below.

## Actual built-image runtime

The product helper and processor were baked into an isolated candidate image; only tests and original fixture inputs were mounted. Assertions run in ordinary Python around actual calibre-debug subprocesses. No public catalog, provider, household library or device was used.

`tests/integration/test_acquisition_calibre_runtime.py` drives two existing owning probes:

1. `acquisition_calibre_runtime_probe.py`: actual Calibre 9.11 title/author matching, explicit-English and missing-language English versus German, distinct edition import, exact prepared bytes under a different source identity, committed replay, removed/replaced format reacquisition, embedded-identifier forgery rejection, and real Calibre bookmark preservation. The outside-library symlink case imports a separate contained record, leaves the external bytes/bookmark intact, and agrees with actual processor receipt verification. Receipt-write failure rolls back membership and recovers without another import.
2. `acquisition_full_runtime_probe.py`: owned loopback OPDS discovery and worker transfer through the full normal ingest subprocess, EPUB processing, KEPUB conversion, direct PDF, actual source/imported hashes, trusted requester membership, receipt retry and source/private cleanup. Two same-title EPUBs with different bodies become separate records. A seeded legacy retention result is reinspected by both processor and helper. Original web annotation, bookmark, reading status and historical receipt retain all stored fields unchanged after normal startup migrations; the selected edition contains its own chapter text and inherits none of the old reader state.

The full probe's fixture preparation was corrected to use the canonical webreader source and migrated annotation/device state before taking the preservation snapshot. Failed fixture datums were retained; preservation assertions were not relaxed.

Final required CI and immutable image references belong in the PR body. UI visual/locale matrices are not applicable because this slice changes no frontend rendering or strings. This does not expand real remote download-client compatibility claims. Later EPUB ZIP-packaging comparison has its own verification record and does not normalize XML or text.
