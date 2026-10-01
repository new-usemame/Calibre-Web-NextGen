# PR #2332 dependency and license review

## Decision needed

This adoption retains four bundled Literata WOFF2 font files from the contributor's PR and adds their SIL Open Font License 1.1 text at `cps/static/fonts/literata/OFL.txt`. The source tree has no new runtime dependency or external font service URL. The font files are shipping assets under a newly introduced license, so this triggers the operator-only licensing gate in `agent-context/AGENTS-DETAILS.md` rule 6. The review branch is prepared for that decision; merging or releasing bundled builds requires the operator to choose whether Calibre-Web-NextGen may distribute these font assets under OFL 1.1. This note records the gate; it is not distribution approval.

## Evidence and terms

- The full OFL 1.1 copyright and license text accompanies the assets.
- The included terms permit embedding and redistribution with software when each copy carries the copyright notice and license. The font software itself must remain under OFL; neither it nor an individual component may be sold by itself.
- No reserved font name is listed in the included text.
- All four WOFF2 files parse as Literata regular, italic, bold, and bold italic; the independent metadata inspection and hashes are recorded in the root review artifact.
- Files remain unmodified as supplied by the contributor. The original commit author is preserved (`sgreadly`); the changelog fragment credits `@sgreadly`.

## Scope

No package or runtime dependency was added, and font requests are served from this application's same-origin static assets. If the operator declines the license gate, remove the four WOFF2 files, the license file, and the Literata reader option before merging or releasing. If accepted, preserve the license file with the fonts in future distributions.
