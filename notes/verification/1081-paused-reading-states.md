# Personal paused reading states (#1081)

Books can be marked **Did not finish** or **On hold** in either interface. These choices apply to one reader and preserve positions, highlights, notes, bookmarks, start counts and history. Background browser, Kobo and KOReader progress can advance a position without clearing the pause. Choosing another status or opening the ordinary reader explicitly resumes; lookup preserves it. Unread retains the existing position-reset behavior.

## Product decisions

Two separate states express abandoning a book and intending to return. Exact filters, Advanced Search, magic-shelf rules, OPDS feeds and table labels expose both, while existing Unread views retain their historical inclusion of active reading and exclude paused books. Existing API booleans are preserved; an additive canonical `read_status` and explicit status endpoint carry all five states.

A Calibre boolean read column cannot represent either pause. Pausing therefore changes only the reader's personal overlay and leaves the shared column untouched. Explicit Finished/Unread keeps existing shared-column behavior. An explicit Currently reading resume clears a shared finished marker. Kobo receives a ReadyToRead compatibility projection and KOReader's three-state library receives unread; saved positions and the personal server state remain intact.

Explicit status intent has a separate nullable timestamp, so newer device activity on an older duplicate cannot outvote a later pause or resume when books merge. The upgrade snapshots existing status timestamps once as an inferred historical baseline. Future automatic reports do not stamp explicit intent. This additive schema change was chosen after independent reverse-clock counterexamples showed that the activity clock alone could lose the user's choice.

## Verification

Independent refutations drove repairs for reverse-clock duplicate arbitration, Classic JSON escaping, actual WebKit selector height, lookup preservation, and integration with Stop reading. The final frozen feature passed 10,357 smoke/unit cases with 102 skipped and only the two Mac SQLite replacement/I/O failures previously reproduced on clean main. The separate serial integration lane passed one case. The first current-main integration passed 67 targeted checks; the subsequent catalog-search/translation rebase passed 125 focused status, locale and classifier checks with one existing skip; Linux CI remains the merge gate.

A complete Linux image (`sha256:f1a292ecb25b627e9c23d1d4025153fe3057700973167944131dd93e91898c99`) from source `8b8fa250cb4abb596d59dd09b8097d0e6dd1ab2d` passed 13 committed browser cases across Chromium desktop/phone and native Safari/iPhone, without retries or skips. Two independent native cases exercised the real gear-menu Stop action, lookup, ordinary resume, position/history preservation, live Unread invalidation and idempotence. Native selectors measured 44 CSS pixels and retained dropdown arrows.

Four additional native tests covered eight authenticated French/Dutch × light/dark × desktop/iPhone cells. Translated controls, save/help messages, Classic badges and table DNF→On hold→Currently reading updates were observed through real navigation. Source files, served JS/CSS and compiled locale hashes matched before and after. Representative compressed captures were inspected. Every final browser run retained strict zero-pageerror assertions.

A populated old-schema clone established cold-boot migration and restart idempotence: historical pause intent was captured once, later activity did not replace it, and a future automatic row retained NULL intent. The current integration preserves that migration body and startup order. The subsequent rebase includes the independently owned catalog-search and translation additions; paused runtime bytes are unchanged.

## Limits

Rapidly leaving an intermediate SPA page before background requests settled produced an existing WebKit auth-probe unload error in a private accelerated flow. The final complete flow awaited its visible state and settled requests, retained zero-pageerror assertions, and passed. No auth-layer repair is claimed. Real Kobo/KOReader hardware, VoiceOver, proxy-subpath behavior and a published release image were not exercised. Protocol and persistence semantics are covered at their behavioral seams; they are not a physical-device claim. No release or deployment was performed.
