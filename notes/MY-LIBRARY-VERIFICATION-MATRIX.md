# My Library (#1939/#1947) — verification matrix

**Why this file exists.** A feature is not handed over for acceptance testing
until it has been driven through the real UI on a real container, in every
account state it can be in, **including across subsystem boundaries**. This file
is what makes that auditable — it says what is proven, by what, and what is not.

It exists because the previous handover failed exactly there. My Library was
tested. Annotations were tested. **Their intersection never was**, and that is
where the defect lived: removing a book from a personal library preserved the
annotation rows perfectly and made them unreachable (404 on the annotations page,
empty device views). Fixed in #2057. Every existing preservation test stayed
green throughout, because they queried rows directly and exercised a different
function (`set_library_mode`, not `remove_book`).

**Reading rule:** "tested" means an assertion runs against the real behaviour.
A row query is not a test of a read path. A unit test is not a test of a UI.

## Account states

Membership behaviour is a function of BOTH the mode and the role, and the two
gates compose. This is also why Global Library can appear to be missing:
`Sidebar.tsx:111` requires `personalLibrary && me.role.browse_global`, so an
account holding neither sees no entry point — which is correct, not a bug.

| id | `library_mode` | `browse_global` | who this is |
|----|----------------|-----------------|-------------|
| A | monolibrary | no | every account by default; nothing auto-migrates |
| B | personal_library | no | administrator-managed selection |
| C | personal_library | yes | self-service; the only state that sees Global Library |
| D | admin | yes | manages other accounts' modes |

State **B** is the most under-tested and the easiest to get wrong: it cannot
empty its own library (`user_library.py:184` refuses the last book without
`browse_global`) and it has no Global Library entry point.

## Operations

1. mode switch A→C/B (seeds every visible book; commits the seed fence)
2. mode switch back to A
3. add one book from Global Library
4. remove one book
5. **remove the LAST book** (empty library — needs `browse_global`)
6. bulk add / bulk remove
7. re-add a previously removed book
8. upload a book (joins the uploader's set)

## Surfaces that must be checked after every mutating operation

catalog + counts/facets · Global Library · book detail · classic UI equivalents ·
OPDS · **Kobo sync set** · shelves · search · covers · downloads ·
**annotations view** · **annotation exports (md/csv/json)** ·
**annotation device views** · reading progress + bookmarks

Bolded surfaces are the ones scoped by membership, so they are where a curation
boundary can silently eat user-owned data.

## Coverage today

| area | proven by | state |
|---|---|---|
| membership storage across remove/bulk/empty/re-add | `test_annotations_survive_library_removal.py` (6 tests, both mutations run) | **proven** |
| annotations reachable after removal | same file — view, all 3 exports, device views | **proven (unit)** |
| mode round trip preserves annotations/hidden/sync/roles | `test_my_library_backend_1939.py` | **proven** |
| seed under `NETWORK_SHARE_MODE=true` | `test_my_library_backend_1939.py` (F-be8891) | **proven** |
| two accounts see independent selections; discovery filter | `my-library.spec.ts` | **proven (e2e)** |
| classic-theme parity of library/global/add/remove | `my-library.spec.ts` | **proven (e2e)** |
| **My Library × annotations, end to end in a browser** | `my-library.spec.ts` — UI removal, device view, annotation page, md/csv/json downloads | **proven (e2e; #2057 red-tested)** |
| **My Library × Kobo sync set after removal** | `my-library.spec.ts` — real delivery/ack, UI removal, archived `ChangedEntitlement` | **proven on a Kobo-enabled container (wire + UI; sync-scope red-tested); SKIPPED in CI — no fixture sets `config_kobo_sync`** |
| **state B (managed, no browse_global) in any e2e** | `my-library.spec.ts` — composed mode/role, no Global Library, exact last-book refusal | **proven (e2e; guard red-tested)** |
| **empty-library UX in a browser** (emptying a curated library completely) | `my-library.spec.ts` — final UI removal, empty state, recovery link, re-add | **proven (e2e; desktop + mobile; last-removal red-tested)** |

## Open decision

`_resolve_book_or_404` also guards `annotations_create` (POST), `annotations_edit`
(PATCH) and `annotations_delete` (DELETE), so #2057's membership bypass is not
read-only. Edit/delete of your own retained notes is intended. **Creating** a new
annotation on a non-member book is broader than the reported need; it is kept
because the global archive is the source of truth, and is flagged rather than
assumed.
