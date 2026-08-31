# My Library backend policy audit (#1939)

`CalibreDB.common_filters()` is the membership policy funnel. Each account is
always in one named mode:

- `monolibrary`: `User.has_own_library == 0`; the membership predicate is
  inactive and the account continuously follows the global archive, including
  later ingests.
- `personal_library`: `User.has_own_library == 1`; a request-cached SQLite
  `json_each` predicate selects the account's durable membership rows.

The one JSON bind avoids expanding a whole library into SQL literals. JSON
function support is detected once per process across both SQLite engines; a
bare-metal SQLite build without it falls back to a correct expanded `IN`
predicate instead of failing every personal-library listing.
`allow_show_global=True` is the explicit bypass used only by the
permission-gated global catalog and first seed. `User.user_library_seeded` is
the independent durable seed-once fence: row count is never used as an
initialization signal, because an initialized user may curate down to zero.
Mode switches never delete membership rows.

`ROLE_BROWSE_GLOBAL` means “may see the whole archive.” It is the single
capability behind both Global Library and self-service mode switching. An
account without it has an administrator-managed mode; an administrator may
still switch that account. No account is automatically migrated on upgrade or
creation: every account defaults to `monolibrary` until an explicit action.

## Direct `Books` query assignments

| Entry point | Assignment | Reason |
|---|---|---|
| `kobo.py` library sync and `api/kobo_two_way.py` picker | Membership-aware | The device sync set and user picker are the account's library. Sync reconciliation intersects Kobo shelves with membership. |
| `kobo.py` changed-reading-state hydration | Deliberately global | Device-trailing per-user state must remain resolvable after removal. |
| `opds.py` feeds, stats, metadata, covers, and downloads | Membership-aware | OPDS is a user-facing catalog and already funnels through the OPDS common filter. |
| `shelf.py` and `api/shelves.py` add, series-add, browse, reorder, and picker paths | Membership-aware | A visible shelf cannot introduce or reveal a book outside the viewer's set. Activity-log title hydration is deliberately global and non-authoritative. |
| `api/info.py` book and author/tag/series counts | Membership-aware | Every visible count describes the caller's catalog; monolibrary retains the historical global payload. |
| `web.py` listings, author/series/tag/publisher/language facets, searches, matching tags, details, and covers | Membership-aware | These are user-visible browse paths already using the common filter. |
| `api/browse.py` author/series/tag/publisher/language facets | Membership-aware | Each entity and its displayed book count are built from filtered Books, so opening a facet cannot produce a smaller set than its label promised. |
| `magic_shelf.py` rule evaluation | Membership-aware | Rules select from the user's library. The raw page hydration is safe because its IDs come from the filtered rule query. |
| `helper.py` e-mail/send-to-device, download, book/series covers | Membership-aware | These user-facing content paths must agree with listing visibility. The documented admin download fallback remains a deliberate curation-only bypass. |
| `helper.py` conversion, upload/rename, and edit helpers | Deliberately global | Role-gated metadata edits change the one global record and file. |
| `progress_syncing/`, `services/annotation_sync/`, and `tasks/annotation_sync.py` | Deliberately global | Bookmarks, annotations, and progress survive membership removal and reconnect on re-add. |
| `tasks/hardcover_sync.py` and `tasks/auto_hardcover_id.py` | Deliberately global | Hardcover metadata is archive-level; its user reading data is preserved independently. |
| `admin.py`, `about.py`, `duplicates.py`, `duplicate_index.py`, and `tasks/duplicate_scan.py` | Deliberately global | Administration and library-health operations cover the archive. |
| `editbooks.py` | Deliberately global and role-gated | A metadata or file edit affects the shared book. |
| `tasks/thumbnail.py`, `tasks/metadata_backup.py`, `tasks/database.py`, `services/cover_preview_cleanup.py`, and `tasks/kepub_package_repair.py` | Deliberately global | Maintenance must process every global record and file. |
| `api/books.py`, `search.py`, and `user_library.py` | Membership-aware except explicit global operations | Ordinary API/search queries use the common filter; seed/add validation use the named global bypass. |

Membership is a curation boundary, not a complete authorization boundary. The
Kobo reading-state GET/PUT and DELETE paths keep `enforce_policy=False`, and
entitlement ownership plus annotation authority remain based on existence in
the global Calibre library. This is required so a removed book's annotations
and ownership survive and can resume when the membership row returns.

Public shelf visibility does not grant membership. A viewer sees the
intersection of the public shelf's links and that viewer's current library;
therefore a public shelf may be partially populated or empty for different
viewers. The shelf owner does not confer their personal membership on anyone
else.

## Mode-switch preservation contract

| State | Switch behavior |
|---|---|
| Membership | Rows remain dormant in monolibrary and return unchanged in personal mode, including a deliberate zero-row set. |
| Shelves | No shelf or `book_shelf_link` row is touched by a mode switch. A separate explicit book removal still drops that user's affected shelf links. |
| `kobo_synced_books` | Untouched; the first personal-mode seed prevents spurious ChangedEntitlement removals. |
| Reading state | `book_read_link` and `kobo_reading_state` are untouched. |
| Annotations | Annotation rows and their sync/authority children are untouched. |
| Hidden/archived | `user_hidden_book` and `archived_book` are untouched. |
| Sync settings | Kobo shelf mode, OPDS shelf mode, two-way annotation settings, and Hardcover token remain unchanged. |
| Roles | The role mask, including global browse, remains unchanged. |

A global book deletion is different from a mode switch. The authoritative
per-user/book purge removes every `user_library_book` row for the deleted
Calibre id, including dormant rows held by monolibrary users. Thus dormant rows
never point at a globally deleted book. User deletion goes through the same
enumerator rather than relying on SQLite foreign-key cascades, which are not
enabled by this application.

A successful classic or API browser upload carries the authenticated uploader
through the ingest sidecar. The resulting global book is idempotently added to
that uploader's personal membership. The sidecar also snapshots personal mode,
so a temporary switch to monolibrary while the queued import runs cannot lose
the uploader's membership; watch-folder imports have no uploader and remain
global-only.

The Kobo #468 deletion fail-safe is source-sensitive. An unreliable magic-shelf
query suppresses deletions only when shelf-sync mode used magic shelves to form
the allowed set. With shelf sync off, personal membership is the sole scoping
source, so a magic-shelf failure does not indefinitely suppress a deliberate
My Library removal.

## Backend route contract

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/v1/library/global` | Permission-gated global listing with `in_my_library` on each book; `filter=not_in_my_library&sort=new` is recent global discovery. |
| `PUT` | `/api/v1/books/<book_id>/my-library` | Idempotently add a globally visible book. |
| `GET` | `/api/v1/books/<book_id>/my-library` | Return removal impact for confirmation. |
| `DELETE` | `/api/v1/books/<book_id>/my-library` | Remove membership and the user's ordinary shelf links only. |
| `POST` | `/api/v1/account/library-mode` | Switch the current account using `{"mode":"monolibrary"}` or `{"mode":"personal_library"}`. |
| `POST` | `/api/v1/account/my-library-intro/dismiss` | Durably dismiss the current account's introductory card. |
| `POST` | `/api/v1/admin/users/<user_id>` | Admin update; accepts the named `library_mode` for the target user. |
| `POST` | `/api/v1/admin/my-library/migrate` | Explicit seed-once migration; optional `user_id`, otherwise all accounts, with a per-account report. |
| `GET` | `/global-library[/<sort>[/<page>]]` | Classic global listing; `recent-missing` orders new global books absent from the caller's set. |
| `POST` | `/ajax/mylibrary/<book_id>/add` | Classic add action. |
| `GET` | `/ajax/mylibrary/<book_id>/removal-impact` | Classic confirmation impact. |
| `POST` | `/ajax/mylibrary/<book_id>/remove` | Classic remove action. |
| `GET, POST` | `/me` | Classic account page; both named modes are selectable by the user. |
| `GET, POST` | `/admin/user/<user_id>` | Classic admin user editor; both named modes are selectable for the target. |

The `/api/v1/auth/me` and `/api/v1/account` payloads expose `library_mode`,
`my_library_seeded`, `show_my_library_intro`, `role.browse_global`,
`can_switch_library_mode`, and `library_mode_managed`. They do not expose the
implementation-shaped `has_own_library` boolean. The intro dismissal lives on
the User row and survives process restarts. The global
listing remains available when an authorized account's set is empty, so the
separate SPA lane can render the required explanatory empty state with a
recovery action rather than a blank grid.

Any response that calls `common_filters` is marked user-specific and leaves
the app with `Cache-Control: private, no-store` plus `Vary: Cookie,
Authorization` and the configured reverse-proxy login header. `/auth/me`,
`/account`, About counts, and removal impact set the same marker explicitly.
This prevents a shared cache from crossing users even when header login never
touches Flask's session.

## Performance evidence

An in-memory SQLite benchmark populated both app and Calibre schemas with
20,000 books and 20,000 membership rows, then timed policy construction plus a
sorted 60-book listing over 15 cold request contexts after two warm-ups:

| Mode | Median | Mean | p95 |
|---|---:|---:|---:|
| `monolibrary` | 2.416 ms | 2.427 ms | 2.536 ms |
| `personal_library`, JSON, 20k members | 6.404 ms | 6.409 ms | 6.683 ms |
| `personal_library`, no-JSON fallback, 20k members | 60.875 ms | 53.433 ms | 62.594 ms |
| recent global missing, JSON, 20k members | 6.293 ms | 6.297 ms | 6.406 ms |

The measured JSON median delta against whole-library mode is 3.988 ms. The
fallback is intentionally slower but correct, and is used only where SQLite
lacks the JSON functions. Recent-missing discovery reuses the JSON predicate
rather than materializing 20,000 SQL parameters. Leg 2 separately measured
1.213 ms for first membership-expression construction and 0.238 ms from the
same-request cache on the second call.
