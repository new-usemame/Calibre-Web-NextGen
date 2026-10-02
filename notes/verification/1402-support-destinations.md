# Host support destinations (#1402)

The administrator can keep the project destinations, replace them for readers with a host URL and label, or hide reporting/donation destinations without a replacement. Documentation and What's New remain available. Admins retain project destinations because they administer and troubleshoot the software. Defaults preserve existing installs. The same policy feeds the account API, classic support menus and New UI Help; it also gates the Ko-fi announcement so a hidden destination cannot reappear elsewhere.

The existing UI Configuration form saves the setting. Partial posts preserve it through a presence sentinel. Absolute HTTP(S) URLs are supported; credentials, whitespace, malformed ports, Unicode control characters and oversized URL/label values are rejected before other submitted settings mutate. No external destination is fetched. A PRAGMA-guarded migration runs idempotently and preserves the original settings row.

## Behavioral evidence

The new menu test was seen red on the previous product image, where project destinations stayed visible under a host-only policy. Its final portable run passes five cases, including setup, at 1280 and 375 pixels: authenticated reader default/host/empty modes, retained docs/updates, external-link attributes, keyboard Tab/Escape and restored trigger focus, light/dark header axe, Ko-fi eligibility, and admin retention. Only the `/me` support projection is intercepted so global settings are not changed in parallel CI; authentication and account roles remain real.

A separate isolated private-rig flow passes two cases (setup plus persisted flow). It uses the actual CSRF-protected UI Configuration Save and `/api/v1/auth/me`, confirms reader/admin policy in both interfaces, and rejects `javascript:` before an unrelated title edit persists. An empty-cookie Guest browser receives the host destination when anonymous browsing is enabled. The probe restores support settings, anonymous browsing and reader theme in finally, then reaps its owned account. A further two-case capture pass expands the actual classic profile menu at both widths. Fourteen named JPEGs cover the configuration form, New UI light/dark menus, classic caliBlur menus, admin override and Guest. No physical-phone or interactive classic default-theme claim is made; default-theme output is covered by full-template rendering tests.

Final frontend unit/TypeScript lane: 192 passed; production bundle build passed. Feature backend checks: 19 passed; original combined backend checks: 108 passed, one skipped. Updated feature plus classic rendering checks: 25 passed. The old classic checks inspected literal source and became stale when the link moved into a macro; two failed and three passed vacuously. Six replacement cases render the full layout with the actual template-filter blueprint and a real User in each theme: exactly one safe translated default link, escaped host replacement, and no link for hidden/empty policy. Existing assertions are preserved as output behavior.

One full local backend suite: 10,286 passed, 102 skipped, one deselected, four failures. Two are the previously reproduced clean-main metadata-replacement SQLite I/O errors. The two stale classic source checks are corrected as above; all affected rechecks pass. Final CI remains the full-suite gate. All shipped locale catalogs compile; new strings have French/Dutch translations and are extracted into the POT.

## Independent review and artifact identity

Independent review found two initial gaps: the Ko-fi banner bypassed the Help policy, and C1/leading control characters could be trimmed or accepted. Both are fixed; final implementation review is clear. Review scope and execution limits are recorded in the PR.

The private image was built as one complete product artifact from `8977f7e4c`, with no source overlays. Container `c887446ead5d`, image ID `sha256:7338a36973e9720a0d5e5b78bed23452544a543aaab55b0a5210e4c9397540f0`. Served policy, configuration template and auth hashes matched the worktree; index and bundle fingerprints are preserved in the private evidence record. Subsequent changes are test/POT/documentation only. The private seed contains invalid old EPUB files whose KEPUB backfill startup errors are pre-existing; the exercised configuration and support requests do not produce errors.

Large logs, JSON evidence, temporary private probe and named JPEGs are retained outside the repository. This PR adds no dependency. The operator owns merging and release.

## Serial merge integration, 2026-10-02

Rebased onto `f4f3ddbe4`, the translation follow-up to #2400 squash `0fd14f768`. The admin-save conflict retains the existing Boolean-restriction compatibility validator and the support URL/label validator before any settings mutation. All base changelog rows and SPA strings remain present. The measured request/import closure is 214 of 272 modules. Focused support policy, complete classic layout, Boolean restriction, locale/catalog compilation and classifier checks pass 114 cases with one pre-existing skip; all 193 frontend units, E2E TypeScript and the production build pass. The original product implementation is unchanged; fresh exact-head CI is required before merge.
