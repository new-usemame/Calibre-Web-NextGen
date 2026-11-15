# PR #2332 dependency and license review

## Distribution cleared — 2026-10-01

The operator cleared distribution of these Literata assets under SIL Open Font License 1.1 on 2026-10-01. This resolves the separate asset-distribution gate in `agent-context/AGENTS-DETAILS.md` rule 6. The adoption retains the contributor's four unchanged WOFF2 faces and the full copyright and license text at `cps/static/fonts/literata/OFL.txt`. No runtime dependency or external font service URL is introduced. Future distributions must preserve the accompanying license and contributor credit. Code, integration and current-head CI remain separate merge gates.

## Evidence and terms

- The full OFL 1.1 copyright and license text accompanies the assets.
- The included terms permit embedding and redistribution with software when each copy carries the copyright notice and license. The font software itself must remain under OFL; neither it nor an individual component may be sold by itself.
- No reserved font name is listed in the included text.
- All four WOFF2 files parse as Literata regular, italic, bold, and bold italic; the independent metadata inspection and hashes are recorded in the root review artifact.
- Files remain unmodified as supplied by the contributor. The original commit author is preserved (`sgreadly`); the changelog fragment credits `@sgreadly`.

## Scope

The clearance covers the four bundled Literata regular, italic, bold and bold italic WOFF2 faces in this adoption, together with the complete OFL text and existing contributor credit. Preserve all of these in future distributions. Font requests are served from this application's same-origin static assets; no package or runtime dependency was added.
