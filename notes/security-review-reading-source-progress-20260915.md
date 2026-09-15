# Reading-source progress: bounded security/seam review (2026-09-15)

Worktree reviewed: `repo/.worktrees/reading-source-progress` (owner branch `feat/reading-source-progress`). Read-only review; no code edits.

## Verdict
No cross-user data or connector-secret leak found in the new route. The review's one medium functional visibility finding is resolved: reading sources now uses the same hidden, archived, and role-gated global deep-link policy as SPA book detail while retaining `common_filters()` ACL/content restrictions. A focused regression covers that contract.

## Checks that passed by source inspection
- Auth: route has both API auth decorator and `_require_real_user()` (`cps/api/reader.py:136-147`), so anonymous/guest cannot read positions.
- Device isolation: devices are selected by `current_user.id`; positions are constrained to those internal device IDs plus requested book (`cps/api/reader.py:152-164`). URL `source=device:<public_id>` only selects a returned row in the SPA; it is not trusted server input.
- The account-level saved position is filtered by `(user_id, book_id)` (`cps/api/reader.py:189-195`), is labeled `Source not recorded`, and device rows stay separate/read-only.
- Storyteller credentials are keyed only by numeric user ID, with no global fallback (`cps/services/storyteller_source.py:78-97`); response returns only `configured/reachable`, never token or URL (`cps/api/reader.py:166-200`).
- Storyteller redirects are disabled (`storyteller_source.py:61-69`); URL is admin-supplied via env/file per `docs/reading-sources.md`, not reader input. This is an operator trust boundary; no user-controlled SSRF path exists in this feature.
- Exact admission is sound: remote SHA-256 must equal the local EPUB and the reported fragment must be a real archive element ID before href exactness. A same-file Readium synthetic fragment uses explicitly approximate chapter-local progression; mismatch/absent hash produces percentage-only. The frontend rechecks the archive hash.
- Preview write seam is sound: selecting a source only calls `rendition.display`, arms `previewingRef`, and relocated persistence is conditional on that flag (`Reader.tsx:455-463`, `1495-1530`). Device/source rows remain read-only.

## Minor hardening (resolved)
Connector-returned `book_id` is now quoted as one URL path segment before same-host position/file requests.

## Reader engine inventory (origin/main manifests + entrypoints)
| Format | Engine/origin | Version/license | CWNG layer |
|---|---|---|---|
| EPUB/KEPUB | `epubjs` npm (`frontend/src/pages/Reader.tsx:6,1363`) | 0.3.93 lockfile; BSD-2-Clause (`frontend/package-lock.json:1481-1490`) | CWNG React reader, archive fetch, CFIs/progress, annotations/settings/source preview |
| PDF | bundled Mozilla pdf.js (`cps/static/js/libs/{pdf,viewer}.mjs`) | 4.5.136; Apache-2.0 header (`pdf.mjs:1-17`, version `:24700`) | NativeReader iframe + existing classic template |
| CBZ/CBR/CBT | no client archive engine; CWNG server extraction (`cps/api/comic.py`) | Python stdlib `zipfile`/`rarfile`; `comicapi` is metadata/ingest dependency | React `ComicViewer` requests count/page images |
| TXT/audio/other | browser `<pre>`/`<audio>` or classic route | no dedicated reader dependency | NativeReader dispatch |

Design note `notes/FRONTEND-READER-DESIGN.md:26` calls epub.js MIT; package lock is the stronger current license evidence and says BSD-2-Clause.
