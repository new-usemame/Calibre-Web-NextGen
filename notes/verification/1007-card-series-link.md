# Direct series links on book cards (#1007)

## Product decision

A visible series name and position open that series directly. Cover, title and author keep opening book details. These are sibling links with independent keyboard focus; a second destination nested inside the book link would be invalid and unreliable. Selection mode keeps one toggle per card with series as plain text, so clicking it cannot navigate away during selection. Cards without detail navigation keep plain series text; the series detail view still suppresses redundant series labels.

The list API includes the first series relation ID alongside its existing name and index. The UI uses that ID rather than guessing a route from the displayed name. Long names truncate while preserving the position, existing locale/RTL formatting remains, and the series target has a visible focus ring and at least 25px height. No new user-facing strings are introduced.

## Evidence

- An owned before-change image lacked series_id in the real list response and the direct link in the UI; the worker retained this seen-red evidence.
- Worker focused checks: 58 serializer/string checks, 193 frontend cases and production build. Series browser spec: nine cases, plus three keyboard cases including setup; light/dark axe and single-selection-toggle assertions passed.
- Root independently reviewed the final product diff and rebuilt the full worktree image without an overlay. Root corrected the new direct-link test to use a true desktop width as well as phone: 1280×800 and 375×812. The entire series spec passed again, nine cases including setup. Eight compressed JPEGs cover focused series and selection in both themes at both widths; real series relation data is used without a list-response interception.
- Verified product image SHA256: 4ae4b015fc4ba5237175e6b8b452e2ea7c729e4bb948ebee3dadd5d7e62e0bf8. Served bundle index-BP_r1vm3.js SHA256: 3b28dd9db6ad2ec31f391d4fec3f4011ea9ab21a472260760da97560be4aec96.
- Full local smoke/unit: 10,272 passed, 103 skipped, two failed. Both are the known macOS SQLite I/O cases in test_2291_replaced_metadata_db_reconnects.py; there are no other failures.

## Limits

This changes New UI cards. Classic navigation and physical devices were not exercised for this change. Owned private rigs use copied test data and temporary series metadata; shared services and household devices are untouched. Merge requires current-main verification; release remains separately owned.

## Current-main integration, 2026-10-02

Rebased onto `7cd71cb18`, including the separately merged catalog-search work and its translations. All preceding CHANGES rows and SPA anchors are retained. BookCard source/styles, the direct-series browser spec and serializer behavioral tests are byte-identical to the originally reviewed `ff22d3b38`; newer main serializer/accessibility changes and the catalog-search API types are preserved. Current focused serializer/catalog/translation/classifier checks passed 80 tests with one existing skip; frontend units passed all 196 cases, E2E TypeScript and production build passed. Original full-image desktop/phone keyboard, axe and series/selection proof remains applicable.

The earlier current-main head `4a81295fd` passed full CI before main advanced: Fast 10,312 passed/136 skipped, serial one passed, Docker 130 passed/nine skipped, browser 929 passed/106 skipped plus one existing New UI/Classic round-trip retry, server-state five passed, both image architectures green. The flaky first attempt stayed on `/` instead of returning to `/app` after the final navigation; its root cause is unproven. No series assertion failed and no retry was initiated manually. Fresh CI on the newly rebased exact head is the final merge gate.
