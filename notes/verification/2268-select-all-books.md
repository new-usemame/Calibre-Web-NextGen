# Select all books in the current view (#2268)

## Product decisions

Select all means the complete server-matched current Library or manual/smart shelf view, including books not loaded into the grid. Search, filters and viewer visibility remain the same as ordinary results. Global Library remains its explicit separate surface; Personal Library does not silently include the global archive. Discover selects its exact displayed random sample: querying a larger random sample would change the meaning of the current view.

The backend shares existing query paths but retrieves distinct ordered IDs without loading Book objects or their relationships. More than 100,000 matches returns 413 with a translated instruction to narrow the view, rather than silently selecting a partial set. The limit prevents an unbounded browser selection while covering large libraries.

Bulk work uses at most eight concurrent per-book requests. Every failure retains its ID for retry; the announcement bounds the listed IDs to 20 with a remaining count, while retaining the whole failed set. While selection is loading, actions and any already-open Add-to-shelf menu are closed/disabled. Clear, Done, route/filter changes and retry state invalidate late responses. Shelf page reset uses layout timing so the previous shelf cannot populate the new route.

## Verification

- Worker focused backend: 60 passed. Frontend: 193 passed and production build. Independent source/query review personally ran 53 backend cases and found no unresolved blocker after the pending-menu and response-completion fixes.
- Real matching private image: initial six browser scenarios passed with genuine authenticated API results and bulk read mutations. Ordinary pages were deliberately bounded to three/two cards while Select all requests reached the complete server query unchanged. The proof covers hidden-book exclusion, query filtering, manual/smart shelf IDs, full-set changes beyond loaded cards, completed held-response cancellation for Clear/Done/route changes, an already-open shelf menu causing zero writes while loading, and 413 preserving the prior selection. Light/dark accessibility checks passed.
- Original product-proof image SHA256: 0c65dc0504c5b19ac90222ab3e854df439737019e1efcc15d0a65ab264906752. Final complete-worktree recapture image SHA256: 558862e0789f57f187851ad9b9ff4cd181a1fa83865cc6cdc40c7bed3b28bbb9. Served bundle index-0q714ChP.js SHA256: 8313881c5eef35e77d47a555b0ae19d46fb9134effbdd054d8e412709570f10c.
- Root seen-red against clean baseline f658265a6: the ID-only route assertion failed because the old route attempted to serialize IDs as Book cards. This is server-seam red evidence; an original UI red run is not claimed.
- Full local smoke/unit: 10,276 passed, 103 skipped, six failures. Two are the existing macOS SQLite I/O failures in test_2291_replaced_metadata_db_reconnects.py. Four were affected old source-shape/fixture checks: page-reset effects, retry callback and a default-sort stub without ids_only. The stub was corrected, and callback/effect pins were replaced with real browser behavior for failed-item retention/retry and navigation from page two to a new manual/smart shelf's page-one book. Final whole focused file passes 13/13 including setup across actual 1280×800 desktop and 375×812 phone. Six quality-75 JPEGs show full selection, one retained failed book and successful retry at each width. Root independently reviewed the actual desktop-selection and phone-failure JPEGs, source diff and provenance. Thirteen affected Python cases pass.
- Root locale/anchor recheck: 61 passed. French and Dutch catalogs compile, and all seven new translation entries validate their placeholders. Existing unrelated catalog-check findings are outside this change.

## Limits

This adds New UI bulk selection; classic is unchanged. Browser evidence uses copied private test data and owned temporary users/shelves, not production accounts. Existing count/permission policies are reused, without a physical-device sync claim. Current-head merge verification governs landing; releases remain separately owned.

## Broad CI follow-up

Run 36852036795 passed 918 browser cases and failed the desktop/phone beyond-loaded-page precondition: a six-book visible CI library auto-filled both three-card pages before Select all. The test now holds later real page responses until bulk proof is complete, releasing them in finally. It still uses actual server listing/selection/mutations and requires fewer loaded cards than selected IDs; no assertion or product behavior is relaxed. All 13 focused cases passed after the boundary fix. Final cleanup waits for owned route handlers before fixture teardown. A fresh broad CI run is required.

CI follow-up36857278746 again failed the unloaded-page precondition because its page-wide link count included the Discover strip. The count/readiness assertion now scopes to catalog-grid, retaining real select_all IDs, held later page responses and persisted bulk-save/retry proof. Independent follow-up review clear; final focused desktop/mobile13/13 pass. The same broad run had917passed/106skipped and one unrelated reader-columns case passed on retry. No product change in this correction.


## Serial merge integration (2026-10-02)

Rebased onto current main292483452, preserving the catalog-source search, host support, lookup/Stop reading, sharing and direct series card links. Only CHANGES row/link conflicts occurred; all preceding rows and SPA anchors survive. Selection API, Catalog/BulkBar/retry logic and the complete focused selection browser spec are byte-identical to the reviewed original. Shared DB query policy retains newer Boolean restriction/account-aware filtering; shelf UI/API retains current sharing, owner-only device marks, OPDS exposure and single-main landmarks. Card series navigation remains main-identical, with one selection toggle.

Current focused backend/query/permission/retry/catalog/translation/classifier packet:134 passed,one existing skip. Frontend197 passed, E2E TypeScript and production build pass. Measured classifier import closure214 of272 matches the README. Original actual whole-image beyond-loaded-page/failed-ID retry/route-cancellation proof and independent REVIEW-2268 remain applicable to unchanged feature bytes. Fresh exact-head CI required after rebase.
