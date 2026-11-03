# CrossPoint clippings: back up the highlights of a CrossPoint reader

Status: built 2026-11-03 on `feat/crosspoint-clippings`. Code: `cps/progress_syncing/protocols/crosspoint_clippings.py`, `ub.CrossPointClipping` and `ub.CrossPointClippingClock`, `ub.migrate_crosspoint_clippings`. Tests: `tests/unit/test_crosspoint_clippings.py`, the CrossPoint cases of `tests/unit/test_annotation_portable.py`.

## Problem

CrossPoint 2.0.0 has Sync Clippings. When it is on, the firmware calls `<kosync base>/api/v1/clippings/<document>` before its progress request. Any failure there stops Sync Progress for the book. CWNG had no such route, so a CrossPoint user on CWNG had to keep Sync Clippings off.

The API comes from the CrossPoint sync server, crosspoint-sync (`src/routes/v1/clippings.ts`, `docs/API.md`). The firmware client is `src/clippings/ClippingSync.cpp` of crosspoint-reader. Line numbers below are for tag `2.0.0` (`42d58da`).

The routes use the login of every other KOSync route, `authenticate_user`, which checks HTTP Basic. The clippings client of CrossPoint 2.0.0 sends only `x-auth-user` and `x-auth-key` (the MD5 hex of the password), with no Basic header, so CWNG answers it 401. Its progress client sends Basic "for Calibre-Web-Automated compatibility" (`lib/KOReaderSync/KOReaderSyncClient.cpp:28`). A CrossPoint change that makes the clippings client send the same Basic header is a separate pull request to crosspoint-reader. With it, the account password and app passwords both work. The KOSync header login of #2258 is a separate CWNG change, and this one does not need it.

## Contract

Both routes are on the `kosync` blueprint, with the login and book lookup of `/kosync/syncs/annotations`. `<document>` is a KOReader digest or a decimal Calibre book id.

| Route | Purpose |
|---|---|
| `GET /kosync/api/v1/clippings/<document>` | Probe and pull |
| `PUT /kosync/api/v1/clippings/<document>` | Upload and delete |

GET takes `cursor` (an unsigned integer, 0 when absent, else 400), `limit` (1 to 100, default 50) and `format=reader` (no `note` and `color`). `since` is ignored, so a client that sends it gets a superset. The reply is `{document, sync_version: 2, cursor, until, more, items, book_known, calibre_book_id}`. `items` holds the served clippings with `revision > cursor`, in revision order. `cursor` is the revision of the last item, or the request cursor when no item came back.

A live item has `id`, `revision`, `spine`, `start_page`, `end_page`, `pages`, `start_word`, `end_word`, `words`, `para`, `chapter`, `text`, `created_at`, `deleted: 0`, `updated_at`, `layout_signature`, `start_offset` and `end_offset`. A tombstone is `{id, revision, deleted: 1, updated_at}`.

PUT takes `{"items": [...]}` with 1 to 50 items and a body of at most 2 MiB. CWNG checks every item first. One bad item refuses the request with 400 and stores nothing. The reply is `{document, until, accepted, matched, calibre_book_id}`. `accepted` counts every valid item, also an item that changed nothing.

An unknown book is never an error. GET returns an empty page with `book_known: false`. PUT returns `accepted` equal to the item count and `matched: false`, stores nothing, and logs a WARNING.

The firmware fails the whole sync when one of these rules fails, so the server keeps each one:

- It pulls from `cursor=0` with `limit=1` until `more` is false. So `items` holds 0 or 1 item, and a revision is never given to two items of one account.
- The new `cursor` is never less than the old one, and it is greater when an item came back. `more` is never true with no item.
- `deleted` is the integer 0 or 1. A JSON boolean fails.
- The numbers are unsigned: `revision` uint64, the page and word fields uint16, `created_at` and `layout_signature` uint32. `para` is null or uint16.
- `text` is 1 to 4096 UTF-8 bytes with no NUL. `id` is 1 to 64 characters from `!` to `~`.
- `start_offset` and `end_offset` are both null, or both uint32 with start less than end and end not 4294967295.
- A reply of 32 KiB or more fails. The largest item (4096 control characters, which JSON escapes to 6 bytes each) is about 25 KiB.

An upload is checked at least as strictly as the firmware decodes a pull. A stored value that the firmware refuses would fail every later sync of that book, and the reader cannot recover from that.

## Why the layout fields come back exactly

The firmware does not set its local `syncRevision` after an upload. So the pull in the same sync finds a different revision and replaces the local record with the server copy (`ClippingStore.cpp`, `applyRemote`). If the server changed a layout field, each new highlight would lose its place on its first sync. So CWNG stores the fields as sent and serves them unchanged. The `chapter` is cut to 64 characters, as crosspoint-sync does. The firmware itself cuts it to 47 bytes.

## Storage

Each clipping is an `annotation` row of the new source `crosspoint`. The route stores it through `koreader_annotations.apply_push` as a highlight with a `text_quote` of its words. So CWNG places the quote in the library EPUB, the web reader shows it with the name of the reader, and sync targets (Hardcover) get it. A quote that is not found stays as the only anchor of the row (`position_type` `text_quote`). Placement takes at most 4000 characters of the text.

The sidecar table `crosspoint_clipping` holds the layout fields, the full text, a `deleted` flag and a `sync_revision`, one row for each `annotation` row. It copies the pattern of `kobo_annotation_materialization`. A row with no sidecar is never served, because the firmware refuses an item without layout fields.

`crosspoint_clipping_clock` holds one revision counter for each account. Each upload that changes a sidecar, and each tombstone, takes the next value. The UPDATE of the counter takes the SQLite write lock, so two writers never get one value. A unique index on `(user_id, sync_revision)` guards it. A counter row, not `MAX()+1`, makes sure a value is not given out again after a hard delete.

A book delete and an account delete remove both tables. The sidecar is an annotation child in `user_book_data._annotation_children`, because a bulk delete skips the ORM cascade. The clock is in `user_account_data`.

`apply_push` commits the annotation row by itself. If the sidecar write then fails, the reply is 503 and the reader sends the item again on its next sync. That retry finds the annotation unchanged and writes the sidecar.

Clippings are keyed on the Calibre book, not on the document hash. A clipping survives a new checksum of the same book. The sidecar keeps the last document hash for logs only.

## Ownership

A clipping goes back only to the reader that made it, by `X-CWNG-Device-ID`. This is the own-device rule of v4.1.48 for Kobo highlights.

| Request | Served clippings |
|---|---|
| `X-CWNG-Device-ID` names a known reader | Its own clippings and those of no reader |
| `X-CWNG-Device-ID` names no known reader yet | Clippings of no reader |
| No `X-CWNG-Device-ID` | Every clipping of the account for the book, as crosspoint-sync does |

GET resolves the reader read-only and never registers one. PUT registers it, as the other KOSync writes do.

A delete is a tombstone, and a tombstone always wins. A later upload of the same id is accepted and changes nothing, because `apply_portable` never brings back a hidden row. A delete of an id that CWNG never stored is accepted. The firmware sends one when an upload failed and the user then deleted the clipping.

A web reader delete reaches the reader. Before it pages, GET turns each served clipping that is hidden, and not yet a tombstone, into a tombstone with a new revision.

Only this route can make or delete a `crosspoint` row. `/kosync/syncs/annotations` refuses `source: crosspoint` on a pushed annotation and `delete_source: crosspoint`. An upload or delete through this route of an id that belongs to another source, or to another reader, is accepted, logged, and changes nothing.

## Not done

- Clippings on a Kobo, and Kobo highlights on a CrossPoint reader. The own-device rule blocks both. See #1942.
- The all-books `GET /api/v1/clippings` route. The firmware does not use it.
- Paging by `since`. CWNG pages only by revision.
- A clean-up of tombstones. A tombstone keeps its layout fields.
- A change to the firmware. The CrossPoint fix that sends Basic on clipping requests is a separate pull request to crosspoint-reader.
- The KOSync header login (#2258). With it, CrossPoint 2.0.0 could sign in on clipping requests with an app password. It is a separate change.
