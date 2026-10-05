# Literata in both EPUB readers — verification

The original contribution by @sgreadly is preserved with its author and cherry-pick source. Literata is an additional optional reader choice; existing saved choices and Book default remain unchanged. Four same-origin WOFF2 assets provide regular, italic, bold and bold italic. Their full OFL 1.1 text accompanies them. No external font request or runtime dependency is introduced. The operator cleared distribution of these assets on 2026-10-01; `notes/dep-review-2332.md` records the clearance and preservation requirements.

## Observed behavior

Whole-branch image: `7086ddf1ce69e531aaff7e6d873fc66bac69cc49c88cc4cc8c3a24ff8f66adfd`. Served 969,410-byte SPA bundle SHA-256: `037da0b30ae1c7368908787d2b41d3e7b3e97dc930a26b9cb33c4933b9517519`. Runtime reader-settings Python, Classic template and epub.js matched the reviewed worktree. The container was healthy with zero restarts.

Five focused Playwright cases passed, including setup and New UI/Classic at 1280×800 desktop and 375×667 touch-emulated Chromium. Each actual reader loaded real EPUB prose, selected Literata, received a real font response, observed its loaded FontFace and computed family inside the chapter iframe, navigated to another chapter and reloaded with the saved choice. Only the deterministic EPUB content response was routed; font requests were not intercepted. Four JPEGs preserve both readers at both sizes. Reader-only contexts use the browser’s native user agent before login so Flask’s strong-session pagehide checks measure the actual browser consistently.

Independent review fetched all four fonts over HTTP: 200, WOFF2 signatures and byte hashes matching source. Calibre’s bundled fontTools parsed all four expected Literata styles, each with 1,163 character mappings. Font bytes remain identical to the original contribution. Production and E2E TypeScript checks passed. The settings persistence regression was seen red when the Literata allowlist entry was removed; 42 settings/API and reverse-proxy checks passed.

## Suite and limits

One full local smoke/unit run: **10,271 passed, 103 skipped, 134 deselected, four failed**. Two failures are the previously observed SQLite baseline cases; two missing French/Dutch Literata catalog entries were corrected, with affected locale checks rerun. The brand name remains Literata in both languages.

This is EPUB support. PDF, physical phones and Safari/WebKit were not exercised. A Literata-specific request through a live subpath proxy was not exercised; existing prefix tests passed, and both reader paths use their existing prefix-aware static URL builders. Independent review found no code or behavior blocker. The separate OFL distribution decision was cleared on 2026-10-01; that clearance does not replace the integration and current-head CI gates.

## Current-main integration — 2026-10-02

Rebased onto `d7036f74ea24b2176e972a51f5727536e327ed4a`, the translation follow-up after the compact cover-action merge. The rebase had no source conflicts. Runtime feature patches match the previously reviewed adoption; the four WOFF2 assets, complete OFL text and reader browser spec remain byte-identical. The original contributor author and credit are preserved. The public dependency note now records the operator's October 1 distribution clearance, and the central CHANGES row links this adoption and the request.

Current-base verification passed 148 focused settings/API/reader-lookup/prefix/locale/changelog/classifier checks with one existing skip, 197 frontend units, production and E2E TypeScript checks, the production build, and locale/diff/preserved-row checks. The classifier import closure measures 216 of 274 modules. The earlier whole-image proof above is retained and was not rerun for this unchanged runtime integration. Independent bounded integration review, fresh exact-head CI and final current-main ancestry remain required before the authorized serial merge.
