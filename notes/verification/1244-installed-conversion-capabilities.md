# Installed conversion capabilities (#1244)

Conversion choices now come from the installed Calibre input/output registry used by the actual job, including user-installed writers when the existing plugin opt-in is enabled. KFX appears only when an output writer reports it; an input plugin or standalone comic utility does not establish KFX output support. The New UI, Classic editor and both conversion POST paths use the same capability helper. Classic additionally checks the caller’s book visibility before queueing.

## Decisions

The matching sibling `calibre-debug` reports Calibre’s initialized registry using fixed code and the converter’s plugin environment. Registry names are validated, known formats keep their established order, and plugin-only targets follow alphabetically. Existing formats, ZIP/RAR pseudo-inputs and OEB directory output are excluded because the job stores a single book-format file. An unavailable, malformed, unsuccessful, overlong or oversized probe hides Calibre choices; configured Kepubify remains independently available.

Registry work uses the existing blocking pool. Identical concurrent requests share one cooperative result. The cache holds at most 16 identities; successes refresh after 60 seconds, failures after five seconds measured from completion. Registry content/stat and directory changes invalidate immediately. In-place archive edits without registry changes are covered by the success refresh.

The probe bounds combined output at 256 KiB and runtime at eight seconds. On POSIX it owns a private process group, drains nonblocking without reader threads, and reaps its child and ordinary descendants on success or failure. EOF requires successful child exit. This is a resource bound for installed plugin code, not a hostile-code sandbox. Windows descendant-tree cleanup is unclaimed; unavailable nonblocking support fails closed with child/descriptor cleanup.

## Verification

- Focused backend, subprocess, route, classifier and environment checks: **67 passed**. Real executable tests discriminate timeout/output overflow, inherited descriptors, descendant cleanup and failure backoff; their repaired paths were seen red. Calibre’s native `downloaded_recipe` name and OEB exclusion also have seen-red regressions.
- Fresh independent review: **CLEAR** at `581030d1624cb84915f9b66319dc861f0bdb72e1`. Real subprocess boundary/EOF/descendant/backoff probes passed. Eight same-key actual executable requests ran one child while 72 concurrent heartbeat ticks had a largest gap of 7.6 ms. A test-isolation defect was repaired by refreshing gevent’s cached clock; its original failing ordering then passed 9 cases with the original two-second deadline and assertions intact.
- Correct full smoke/unit lane: **10,328 passed, 102 skipped, two failures** in 385.35 seconds. Both failures are the metadata-replacement SQLite disk-I/O cases previously reproduced on clean main in the macOS harness. The separate non-xdist unit lane passed its one case. Current-head Linux CI remains the merge gate.
- Complete Linux image: native Calibre registry reported its installed formats. An owned diagnostic output plugin was absent with opt-in off and available with opt-in on in both interfaces. The final desktop/phone inventory and New UI conversion browser packet passed four cases plus one intentional mobile conversion skip (one owner for the shared proof book).
- Actual New UI selection queued HTTP 200, the background `ebook-convert` job stored CWPROOF, and downloading it returned the exact 55-byte diagnostic writer result. It survived application restart. The diagnostic writer verifies the pipeline; **no real KFX writer or KFX conversion was tested**.
- Removing/reinstalling the diagnostic plugin without restarting the app immediately changed eight parallel inventories: 166 ms removed and 153 ms restored. Health returned HTTP 200 during the check.
- Final default-off restart: both interfaces at desktop and phone widths excluded CWPROOF; crafted CWPROOF and KFX conversion POSTs returned HTTP 400. Four compressed captures were inspected; narrow conversion controls remain usable.

Product source `e07fe4a2b08ffbcf97e37fd8e9a9efc58b83b404` was built into image `sha256:08fc7a4273bd40a6c5a36bcaea16b5ae5ce94485abe978924009ebc76a5d676e`. Subsequent changes are test/documentation only. Seven checked runtime source hashes match the worktree and container. Served bundle `/static/app/assets/index-aRtHK6ty.js` has SHA-256 `82be1fee991a15a859daa23a9b92ab8ff9c7997aefd4060c5915b2d042fb20c9`.

Evidence is retained privately in the feature work state. No schema change, new dependency, bundled plugin or external service. No physical-device, proxy-subpath, Windows tree-cleanup or format-pair-wide promise. Existing pair-specific Calibre/Kepubify limitations are unchanged. No release or deployment was performed.


## Serial current-main integration — 2026-10-02

Rebased onto `b25a10d7c23d8db26f22123119c584c4b75955e6`, including all preceding reader/font/selection/Discover/file-ISBN/export/cover improvements. Runtime rebase head `e856b0490d8775289639fc197e404adfffbb589b` preserves the original independently reviewed capability service and both feature test files byte-for-byte. Changelog resolution retains every current-main row and links PR2429; the classifier README retains current worker/isolated-parser guidance and measures225/284.

Automatic locale merges retained all keys but interleaved the feature entries inside main. FR/NL and POT now retain their complete raw main prefixes followed by exactly the two original capability messages, with original values/flags. No feature behavior or permission change was made during this integration.

Current capability/format-validation/user-plugin/API/translation/classifier packet:161passed/1existing skip38.35s. Original full suite and Linux active-plugin/off/on/hot-change/conversion/restart proofs above remain historical. Independent current-main source disposition, new complete-source artifact with bounded actual conversion behavior, exact resulting-head CI and fresh main/head/persona gates remain required for the serial merge. No release, deployment, original-issue mutation or comment.
