# My Library backend policy audit (#1939)

`CalibreDB.common_filters()` is the membership policy funnel. It remains a
no-op when `User.has_own_library` is false. An enabled account gets a
request-cached SQLite `json_each` membership predicate; the one JSON bind avoids
expanding a whole library into SQL literals. `allow_show_global=True` is the
explicit bypass used only by the permission-gated global catalog and the
pre-enable seed.

## Direct `Books` query assignments

| Entry point | Assignment | Reason |
|---|---|---|
| `kobo.py` library sync and `api/kobo_two_way.py` picker | Membership-aware | The device sync set and user picker are the account's library. Sync reconciliation intersects Kobo shelves with membership. |
| `kobo.py` changed-reading-state hydration | Deliberately global | Device-trailing per-user state must remain resolvable after removal. |
| `opds.py` feeds, stats, metadata, covers, and downloads | Membership-aware | OPDS is a user-facing catalog and already funnels through the OPDS common filter. |
| `shelf.py` and `api/shelves.py` add, series-add, browse, reorder, and picker paths | Membership-aware | A visible shelf cannot introduce or reveal a book outside the viewer's set. Activity-log title hydration is deliberately global and non-authoritative. |
| `api/info.py` book count | Membership-aware | The count describes the caller's visible catalog. |
| `web.py` listings, facets, searches, matching tags, details, and covers | Membership-aware | These are user-visible browse paths already using the common filter. |
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

## Backend route contract

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/v1/library/global` | Permission-gated global listing with `in_my_library` on each book. |
| `PUT` | `/api/v1/books/<book_id>/my-library` | Idempotently add a globally visible book. |
| `GET` | `/api/v1/books/<book_id>/my-library` | Return removal impact for confirmation. |
| `DELETE` | `/api/v1/books/<book_id>/my-library` | Remove membership and the user's ordinary shelf links only. |
| `GET` | `/global-library[/<sort>[/<page>]]` | Classic global listing. |
| `POST` | `/ajax/mylibrary/<book_id>/add` | Classic add action. |
| `GET` | `/ajax/mylibrary/<book_id>/removal-impact` | Classic confirmation impact. |
| `POST` | `/ajax/mylibrary/<book_id>/remove` | Classic remove action. |

The `/api/v1/me` payload exposes `has_own_library` and
`role.browse_global`. The global listing remains available when the account's
set is empty, so the separate SPA lane can render the required explanatory
empty state with a recovery action rather than a blank grid.

## Performance evidence

An in-memory SQLite benchmark populated both app and Calibre schemas with
20,000 books and 20,000 membership rows, then timed policy construction plus a
sorted 60-book listing over 15 cold request contexts after warm-up:

| Mode | Median | Mean | p95 |
|---|---:|---:|---:|
| `has_own_library=0` | 0.414 ms | 0.460 ms | 0.634 ms |
| `has_own_library=1`, 20k members | 4.573 ms | 4.516 ms | 4.854 ms |

The measured median delta is 4.158 ms. Within one request, rebuilding the
membership expression measured 1.528 ms on the first call and 0.262 ms from
the request cache on the second. An earlier ORM-row materialization prototype
measured 42.309 ms median for the enabled case; aggregating membership IDs to
JSON inside app.db removed that Python object cost.
