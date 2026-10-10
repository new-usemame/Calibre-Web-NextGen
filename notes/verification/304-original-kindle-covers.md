# Original-size Kindle covers in the cover picker

Issue #304 asked for cover-picker results comparable to the Kindle High-res Covers plugin. The server now checks Amazon's existing `m.media-amazon.com` image endpoint using the original `MAIN._SCRM_` variant before using the existing bounded `SL2000` image. A missing, non-JPEG, or undersized MAIN image falls through to SL2000. Unknown keys and Amazon's tiny placeholder image remain excluded.

The two shared consumers use the same ordered key resolver. A valid ISBN-10-derived key is tried first; stored Amazon/ASIN identifiers follow, which preserves coverage for Kindle editions without an ISBN. The existing `CWA_COVER_BOOST_AMAZON_CDN` flag remains the single kill switch for the picker candidate and the metadata cover-upgrade path. Metadata-provider search enablement remains independent: the picker can offer a direct Amazon image even if Amazon's metadata provider is disabled.

Applying a result follows the existing validated cover-save path. It downloads the selected candidate, stages the new image, commits the cover metadata and publishes the file with existing rollback handling. JPEG bytes are retained without resizing. No new host, service, dependency, picker endpoint, or provider key is introduced.

## Verification

Focused backend tests passed: `tests/unit/test_cover_booster.py`, `tests/unit/test_cover_picker_service.py`, and `tests/unit/test_cover_image_provenance.py` — 100 passed. A seen-red ordering mutant confirms that reversing the probe order restores the old SL2000 result and fails the MAIN-preference test; the fallback test requires the prior URL after the MAIN probe fails.

Real CDN responses fetched for the reporter’s example ASINs for `B0DHV4TZ4L` and `B0DJ1TV47C` matched the plugin's MAIN images byte for byte. The original images are respectively 1706×2560 (SHA-256 `c8f1651672ba8315a1f8ec38c6a4bd95ee8429cbdd9c7046f87dc778c73e922d`) and 1594×2400 (SHA-256 `c8adee73299482cdc4872201f47a3c51078394e5ea39486b7eff045198cae823`). Their older SL2000 responses are 1333×2000 and 1328×2000. Evidence and source images are archived in the private review workspace.

On the whole-image rig, an isolated editor account with all 15 metadata-provider searches disabled used the New UI picker on an ASIN-only book and the Classic picker on a book with both an ISBN and ASIN. Each displayed the actual ASIN-specific MAIN image, accepted selection/application, returned HTTP 200, and served the stored cover at its original dimensions and byte hash. The candidate `<img>` requests also returned HTTP 200 and loaded at full natural dimensions.

The original whole-image picker/apply proof used the same `cover_booster.py` bytes as the rebased branch. The current base (`ba52c42a38d1e5df8fdc1ab17dea5e2d3ac553f2`) was also built into a fresh whole-stack image and restarted with the existing private rig's config, library, and ingest mounts preserved. The image ID is `sha256:9d3ac7a1cc4db48b505c2b17fdcdc08256cc7adb77ebba4f7cc79002bbbbb9e4`; it booted once and served `index-aRtHK6ty.js` (SHA-256 `82be1fee991a15a859daa23a9b92ab8ff9c7997aefd4060c5915b2d042fb20c9`). The in-image `cover_booster.py` and classic picker script hashes exactly match the worktree (`b4cc219988e4ac46fdf02365a48599fe8815b54a3063ce85de4194590ba776d6` and `738a1992a9434794df1a3dcb50834573ace0e9f18fb0722c6f6f7fd46f6061cd`).

On that current image, a newly created private editor account had every metadata-provider search disabled. The actual New UI ASIN-only book and Classic ISBN+ASIN book each made one live candidate request, displayed the corresponding Amazon MAIN candidate, and loaded the image at its natural dimensions. After the image replacement and restart, authenticated GETs of both stored covers still matched their original byte hashes. This check did not apply or modify either proof cover. Current-image desktop captures and the HTTP/hash packet are preserved in the private verification archive.

The frozen-base full suite reported 10,291 passed, 103 skipped, and 3 failures. Two are the known macOS SQLite baseline failures in `test_2291_replaced_metadata_db_reconnects.py`. The third was `rootUnitLane.test.ts` timing out at 30 seconds during the resource-heavy parallel suite; the isolated rerun of `tests/unit/test_frontend_unit_suites_run.py` passed 19/19, including that lane. No product failure remains from that timeout.

This changes new candidate selection; it does not forcibly replace stored covers or reprocess URLs already classified as high resolution. Preserving the existing ISBN-first order avoids silently changing a print edition to its Kindle edition. MAIN-first keeps original artwork when available while the prior URL retains compatibility for CDN records without it. Live phone-width picker application was not rerun for this backend URL change; the shared picker layout is unchanged, and the measured New/Classic cases were desktop. No release or deployment is included.


## Serial current-main integration — 2026-10-02

Rebased cleanly onto main `357fca016155e22d73e775fb3edbe7bd580ed62a`, the translation follow-up after book-list export. Runtime head `423523e275ef9befe825e5738970319170bebda5` retains the original reviewed cover resolver bytes. The feature remains seven additive files; main SPA anchors/changelog rows are retained and the current import classifier still measures224/283. The central changelog row links PR2428 and issue304.

Current focused cover booster/picker/provenance/ISBN wiring plus classifier packet:141passed30.81s. The shell wrapper then failed assigning zsh's reserved `status` variable; the complete pytest log records the successful terminal result, and subsequent wrappers use a task-specific variable. No extra test rerun or product change is represented by that wrapper correction. No frontend change or repeated full suite is claimed.

Original real CDN, New/Classic desktop picker/apply and full mixed-platform evidence above remain historical. A new complete current-source image and bounded actual cover integration, independent source disposition, resulting-head CI and final current-main/head/persona gates remain required before merging. No release, deployment, issue closure or comment.
