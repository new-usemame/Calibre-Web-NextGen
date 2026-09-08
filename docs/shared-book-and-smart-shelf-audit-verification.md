# Shared book continuation and live smart shelves

Audit baseline: `1c4f7c52ab` (main product content `1ec1bfe677` plus findings ledger).
Fixes F-36b518 and F-7d3396. No release or production/device mutation is claimed.

## Policy

A public shelf already grants visibility of its books under #2078. Its entry now remains usable through book detail, cover, browser reader resources and web/OPDS acquisition without implicitly changing personal membership. The explicit `allow_public_shelf_books` option stays inside `common_filters`, where language, tags and restricted columns still apply. Private shelves never supply the allowance. Reading/download role decorators remain authoritative. Direct resources retain the existing exceptions for a user's own hidden/archived books.

OPDS selected-shelf exposure remains an additional intersection. Native Kobo resources retain their default membership policy; public-shelf browsing does not add native device entitlements.

Book detail provides `accessible_via_public_shelf` for the frontend to distinguish usable shared books from discovery-only global metadata. This field is computed only after the book passes the detail content-policy lookup. Shared readers retain their own progress and annotations, without acquiring personal membership.

Smart-shelf ID queries now reevaluate live rules and account policy before accepting any cached membership. The stored cache remains the Kobo membership-generation record, preserving its timestamp when the set is unchanged, including sort changes. An identical ordered result returns without rewriting cache rows. This avoids trying to enumerate every writer that can alter membership, mode, allowed content, hidden books, or external Calibre rule inputs.

## Evidence

`tests/unit/test_shared_book_continuation.py` uses real attached SQLite schemas, real policy/query/serializer/handler functions and Flask HTTP calls. Authentication is supplied by the isolated fixture; actual web reader/download role decorators and OPDS download-role checks run. Cover-byte delivery and download-file transport are substituted after real authorization; PDF reader bytes are served from a real temporary file. No HTML template/browser/device is simulated as having been tested.

The test follows the serialized shared shelf item into detail, cover, browser download, selected and ordinary OPDS download, PDF bytes, and bookmark save/resume. Negatives include private/unshared books, denied tags, language restrictions, a missing restricted column with an active allowlist, unselected OPDS exposure, public-to-private revocation, revoked reader/download roles, and native Kobo scope. No membership rows are implicitly created.

The smart-shelf sequence warms the real query/cache, removes and re-adds membership, disables/re-enables personal mode, changes denied tags and hidden state, and observes results across fresh Flask requests. No-op reads and additions must preserve the Kobo generation timestamp.

Seen red on baseline: **5 failed, 6 passed**. Failures include the shared detail 404 and stale warm smart-shelf result. Fixed: **11 passed**. The adjacent OPDS, smart-shelf, reader/resume, catalog/shelf APIs, membership, and Kobo policy suites passed **492 tests in 13.94 seconds**. Three obsolete source-string cache assertions were replaced by the real SQLite coverage here and existing `test_magic_shelf_sort_cache_timestamp.py`; source spelling such as `.first()` is not the timestamp-preservation behavior.

An independent upgrade auditor reran their separate two-book warm-cache/removal probe against this fix and observed the correct remaining book. Their review found no new correctness issue in the live-query/generation-preservation diff.

Performance observation, local SQLite, 20,000 matching personal-library books: ID query plus unchanged-generation lookup took 34–83 ms across five warm requests (median 69 ms). This is additional work compared with trusting an old ID list; it buys immediate correctness for every mutation source. It is not a production/device performance claim. The SPA already queries smart shelves live.

Remaining combined gate: frontend shared-book controls, actual classic/SPA browser rendering and readers, and final release/runtime evidence are owned by the integrating audit parent. This document records the backend seam verification, not a declaration that a release was cut.
