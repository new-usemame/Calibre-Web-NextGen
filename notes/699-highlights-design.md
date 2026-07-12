# #699 — KOReader highlights support

Status: design complete; implementation deliberately phased (2026-07-12)

## Scope result

CWNG already ships the clean server/read-only slice requested by #699:

- per-user annotation storage (`ub.Annotation`);
- authenticated `GET /kosync/syncs/annotations/<document>` and `PUT /kosync/syncs/annotations`;
- the portable annotation wire shape and conflict/tombstone logic;
- a per-book Highlights page, count heading, and Markdown/CSV/JSON exports;
- plugin transport and a device-provider seam.

The missing feature is collection from KOReader itself. The bundled plugin's only device provider is `kobo_sqlite_provider.lua`, which reads/writes stock Kobo's `KoboReader.sqlite`. Non-Kobo KOReader devices have no provider. Adding another table or display page would therefore not make one KOReader highlight appear.

The next honest slice is a KOReader-native provider built through KOReader's `DocSettings` and live reader annotation APIs. Direct `.sdr/metadata.lua` writes are deliberately excluded: KOReader supports document-relative, central, and hash-based sidecar locations, and an open reader may concurrently own the metadata.

Primary KOReader references:

- https://koreader.rocks/doc/modules/docsettings.html
- https://koreader.rocks/doc/modules/koplugin.exporter.html
- https://koreader.rocks/user_guide/

## Protocol

No new HTTP routes or dependencies are needed.

1. Reuse progress sync's active-document digest and checksum-to-book resolution.
2. A `koreader_sdr_provider` reads local highlights/bookmarks through KOReader APIs and normalizes them to the existing portable shape.
3. Pull server rows with `GET /kosync/syncs/annotations/<digest>`.
4. Existing `sync_logic.diffAnnotations` computes device-bound and server-bound changes.
5. Push local additions/changes/tombstones with `PUT /kosync/syncs/annotations`.
6. In the later write phase, apply remote rows through live KOReader reader APIs and request a UI refresh; never rewrite a live sidecar behind the reader.

Annotation capability remains optional. An older server's 404 or an unavailable provider must not break kosync authentication or progress GET/PUT.

## Storage and identity

- Server identity remains `annotation_id`, scoped by `(user_id, book_id)`.
- Prefer a stable KOReader-owned highlight/bookmark id. If current KOReader exposes none, derive an id from immutable creation anchors and persist the server id in plugin-owned document settings; never key on editable quote/note/color.
- Reflowable documents require a KOReader xpointer anchor. Fixed-layout documents require page plus geometry. These must not be squeezed into KoboSpan columns without lossless fixtures.
- Existing storage is sufficient for read-only ingestion when text, note, color, deletion, and a portable anchor can be represented. If the anchor cannot be represented losslessly, still allow server display/export but defer placement in the web reader and device-bound sync.
- A durable `hidden=true` row means deletion. Mere absence never means deletion because a document/sidecar may be temporarily unavailable.

## Sync semantics

- Phase A is explicit “Sync highlights now,” KOReader → server only.
- Position/creation anchors are immutable; text, note, color, and deletion are mutable.
- Device clocks are not authoritative across readers. Before bidirectional release, PUT must return a server revision used for deterministic conflict order; current timestamp comparison is compatibility behavior, not the final contract.
- Retries are idempotent per annotation id. Partial application reports per-item results.
- Device-write phases create a bounded backup through KOReader-supported paths before the first change.

## UI surface

- Keep the existing book-detail → Highlights page as the read-only destination.
- Add `annotation_count` to the book-detail payload so the Highlights button itself can show the reporter-requested count without fetching the full list.
- The empty state should later distinguish “no highlights” from “KOReader highlight sync disabled.”
- Rename “experimental, Kobo only” only when a KOReader-native provider reports available.
- Kindle/Crosspoint `My Clippings.txt` is a separate preview/importer beside the Kobo import flow, with book matching, ambiguity review, and idempotent source keys. It is not part of live kosync.

## Phases

### A — read-only KOReader ingestion

- Add version-pinned EPUB/PDF sidecar fixtures for highlight, note, edited text, color/style, bookmark-only row, and deletion.
- Implement provider reads through KOReader APIs.
- Enable explicit push-to-server only.
- Verify authenticate → progress GET/PUT → annotation GET → provider read → annotation PUT → repeated pull → web display.
- Repeat against an older server returning annotation 404 and prove progress still works.

This is the first release-worthy implementation and should require no schema churn if fixtures validate the portable anchor.

### B — server-to-KOReader placement

- Prove lossless anchors for reflowable and fixed-layout formats.
- Apply through live reader APIs.
- Test remote create/update/delete, open/closed document behavior, every metadata storage mode, backup/rollback, and UI refresh.

### C — automatic bidirectional convergence

Add document/highlight event hooks, server revisions, retry queue, skewed-clock multi-device tests, and bounded conflict UI.

### D — import/discovery polish

Add `My Clippings.txt` preview/import and per-book annotation count on the detail button.

## Deliberately not implemented in SP5

- direct Lua sidecar parsing/writes;
- duplicate server storage or UI;
- bidirectional placement without real KOReader fixtures and live-reader API tests;
- bundling `My Clippings.txt` into the live protocol.

Those would add risk without a clean vertical slice. Phase A is the next implementation target.
