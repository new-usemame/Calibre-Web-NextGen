# Kobo two-way annotation sync — authoritative-set design

**Status:** design only; no production implementation is authorized by this document

**Date:** 2026-08-16

**Hardware evidence:** Kobo Clara BW, firmware 4.45.23792

## 0. Evidence labels and executive decision

Every factual statement in this document is marked as follows:

- **[OBSERVED]** means directly measured on Kobo hardware/on the wire, or directly verified in the repository at the time of writing.
- **[ASSUMED]** means inferred, not yet measured, or a proposed behavior whose Kobo compatibility still requires verification.
- **[ASSUMED — RECOMMENDATION]** is the concrete design choice this document recommends. It is deliberately marked ASSUMED until implementation tests and the staged hardware gates prove it.

**[OBSERVED]** On book open and book close, firmware 4.45.23792 performed `PUT /kobo/<token>/v1/library/<uuid>/state`, then a delta `PATCH /api/v3/content/<uuid>/annotations`, then `POST /api/v3/content/checkforchanges`, and finally `GET /api/v3/content/<uuid>/annotations?limit=100` only when `checkforchanges` returned that content ID.

**[OBSERVED]** `checkforchanges` returned a bare JSON array of content-ID strings. An empty array prevented the annotation GET and left the device annotation set untouched.

**[OBSERVED]** A successful annotation GET was an authoritative replacement set. Adding a row created it on the device, omitting a row deleted it, and replaying the original set restored it.

**[OBSERVED]** The device assembled all measured pages before replacing its local set. A first page of five rows followed by a second page of eight rows produced exactly 13 local rows.

**[OBSERVED]** Kobo returned uploaded `location.span.startPath` values verbatim even when two incompatible encodings coexisted in one response. Normalizing this field on ingest would corrupt the round trip.

**[OBSERVED]** PATCH is a delta and cannot establish the complete set. Separate captures with 7 and 88 device highlights each uploaded only one annotation.

**[OBSERVED]** Kobo cloud returned a complete annotation set for a sideloaded book and restored annotations previously absent from the device.

**[OBSERVED]** The Libra Colour backup contains 3,016 annotation rows across 17 volumes. All 3,016—not only the 810 rows whose `ContentID` contains a non-rendering `#` fragment—are exposed to authoritative-set replacement for their books; 810 is only the malformed-location subset.

**[OBSERVED]** The same backup contains 49 stylus/freehand `markup` rows. `ExtraAnnotationData IS NOT NULL` selects exactly those 49 rows and no highlight or note rows; the opaque UTF-16 content begins with a field decoding to `StartKey`.

**[OBSERVED]** The complete Libra type/property split is 2,964 highlights with NULL `ExtraAnnotationData`, 49 markup rows with non-NULL `ExtraAnnotationData`, and 3 notes with NULL `ExtraAnnotationData`. Twenty-four markup rows belong to the Iliad and 11 to the Odyssey, so the opaque-content case occurs in actively annotated books rather than an unused edge fixture.

**[ASSUMED — RECOMMENDATION]** CWNG should own the exchange only after it has atomically captured a complete Kobo-cloud set and proved that capture corresponds to the device manifest. Until then it must answer owned-book `checkforchanges` with `[]`, thereby preventing a destructive GET. It must never answer GET from a partial PATCH-derived set.

**[ASSUMED — RECOMMENDATION]** The authoritative unit is `(user_id, book_id)`, not an individual annotation and not a device. Device-specific acknowledgment state is tracked separately, but all enabled devices converge on one server-owned set for that user and book.

**[ASSUMED — RECOMMENDATION]** The feature ships behind two default-off gates: an instance-wide kill switch and a per-user opt-in. Each book additionally has an explicit authority state. All three gates must be open before CWNG may name a book in `checkforchanges` or serve its GET.

**[ASSUMED — RECOMMENDATION]** Authority is not sufficient for authoring. A separate hard, per-book opaque-content gate must prove `ExtraAnnotationData` absent before CWNG may add, edit, or delete anything in that book's Kobo-served set. A non-NULL value—or unknown property state—blocks Kobo authoring for that book in full, independent of annotation type, while reading-position sync continues normally.

## 1. Existing repository state

**[OBSERVED]** `cps/ub.py` defines `Annotation` as the canonical cross-reader annotation table. It already stores `annotation_id`, `hidden`, `client_modified_at`, `origin_device_id`, `device_origin_id`, `assigned_device_id`, `source`, text/color/note fields, parsed Kobo container paths and offsets, `chapter_progress`, `context_string`, `cfi_range`, and PDF/comic/xpointer variants.

**[OBSERVED]** `Annotation` has no annotation-type column, no exact raw Kobo location, no semantic content revision, and no per-user/per-book authoritative-set or ETag row.

**[OBSERVED]** The current Kobo PATCH path in `cps/services/annotation_sync/__init__.py` parses selected fields into `Annotation`. It stores `startPath` in `start_container_path`, derives and normalizes `content_id` from `chapterFilename`, and applies a valid `clientLastModifiedUtc` as `client_modified_at`.

**[OBSERVED]** The observed GET object places `context` at the annotation top level. The current PATCH ingester looks for `contextString` or `context` inside `location.span`, so the observed top-level shape is not completely represented by the current path.

**[OBSERVED]** The current web edit path updates color/note and `last_synced`, but it does not update `client_modified_at` or a semantic annotation revision.

**[OBSERVED]** `routing_revision` tracks device assignment changes. It is not an annotation-content revision and must not be reused as one.

**[OBSERVED]** `cps/services/annotation_content_id.py` conservatively normalizes the separate derived `content_id`. It does not need to operate on, and must never rewrite, the raw Kobo location materialization proposed below.

**[OBSERVED]** `cps/services/annotation_backup.py` schedules a rolling-three snapshot after `Annotation` inserts and updates. Its current schema version 2 serializer omits `client_modified_at`, device attribution, polymorphic location fields, and every new Kobo authority/materialization field proposed here. It also deliberately does not write an empty snapshot when no annotation rows remain.

**[OBSERVED]** The repository migration convention is model declaration plus an idempotent function called by `migrate_Database()` in `cps/ub.py`. Annotation migrations use `Base.metadata.create_all(..., checkfirst=True)`, `engine.begin()`, live `PRAGMA table_info(annotation)`, `CREATE INDEX IF NOT EXISTS`, and duplicate-column recovery because SQLAlchemy inspector state may be stale after table renames.

## 2. Data model and schema delta

### 2.1 Changes to the generic `annotation` table

**[ASSUMED — RECOMMENDATION]** Add these columns to `annotation`:

```text
annotation_type       VARCHAR(32) NULL
content_revision      INTEGER NOT NULL DEFAULT 1
server_modified_at    DATETIME NULL
last_editor_device_id INTEGER NULL REFERENCES device(id) ON DELETE SET NULL
```

**[ASSUMED — RECOMMENDATION]** `annotation_type` stores a bounded native type token verbatim. The observed values are `highlight`, `dogear`, `note`, and `markup`; `NULL` means unavailable on a legacy row. A future type is preserved rather than rejected merely because it was not produced by the first device tested.

**[OBSERVED]** `highlight` and `dogear` were observed on the Clara wire/device path. `note` and `markup` were observed in the Libra Colour device database.

**[OBSERVED]** The three Libra Colour `note` rows are representable highlight-shaped annotations: `Text` and `Annotation` are populated and `ExtraAnnotationData` is NULL. Note type is therefore not an opaque-content signal and must not block a book.

**[OBSERVED]** No `note` object appeared in the 13-annotation Clara wire capture. That absence does not overturn the Libra device-database observation that note is a representable, non-opaque native shape.

**[ASSUMED — RECOMMENDATION]** Type is descriptive, not the authoring-safety gate. A row with an unfamiliar type may be replayed from an exact authoritative materialization, and its presence does not by itself block authoring elsewhere in that book. CWNG may create only types for which it has a proven serializer.

**[ASSUMED — RECOMMENDATION]** `content_revision` increments for every accepted change to type, text, note, color, native location, context, attachments, or hidden state. It does not increment for device assignment; `routing_revision` continues to own that concern.

**[ASSUMED — RECOMMENDATION]** `server_modified_at` is the server receipt/commit clock for semantic content. `client_modified_at` remains the normalized client-declared UTC clock. Neither is replaced by `last_synced`, which retains its current compatibility meaning.

**[ASSUMED — RECOMMENDATION]** `last_editor_device_id` records the actor used for deterministic conflict handling. It is mutable and does not replace immutable `origin_device_id`.

### 2.2 Exact Kobo materialization

**[ASSUMED — RECOMMENDATION]** Add one adapter-specific row per annotation rather than adding an unconstrained Kobo blob to the generic core:

```text
kobo_annotation_materialization
  id                       INTEGER PRIMARY KEY AUTOINCREMENT
  annotation_id            INTEGER NOT NULL UNIQUE
                             REFERENCES annotation(id) ON DELETE CASCADE
  raw_annotation_json      BLOB NOT NULL
  raw_location_json        BLOB NOT NULL
  raw_client_modified_utc  TEXT NOT NULL
  payload_sha256           VARCHAR(64) NOT NULL
  materialization_revision INTEGER NOT NULL DEFAULT 1
  provenance               VARCHAR(24) NOT NULL
  attachments_state        VARCHAR(16) NOT NULL
  serveable                BOOLEAN NOT NULL DEFAULT 0
  quarantine_reason        VARCHAR(64) NULL
  created_at               DATETIME NOT NULL
  updated_at               DATETIME NOT NULL

  CHECK provenance IN ('kobo_cloud_seed', 'kobo_patch', 'cwng_authored')
  CHECK attachments_state IN ('missing', 'empty', 'nonempty', 'invalid')
  UNIQUE(annotation_id)
  INDEX ix_kam_serveable(annotation_id, serveable)
```

**[ASSUMED — RECOMMENDATION]** `raw_annotation_json` is the exact current UTF-8 JSON replay object. For a cloud seed or PATCH it initially contains the byte-exact object slice received from Kobo, including property order, escape spelling, omitted-versus-present fields, `attachments`, optional `chapterTitle`, and any protocol field not yet modeled. It is stored as BLOB rather than TEXT so the database layer cannot perform a text conversion. It is bounded to 64 KiB, must parse as one JSON object, and must have no trailing non-whitespace bytes.

**[ASSUMED — RECOMMENDATION]** `attachments_state` is derived without interpreting attachment contents: `{}` is `empty`, any nonempty object is `nonempty`, absence is `missing`, and a non-object value is `invalid`. `nonempty` is treated as opaque-content evidence; `missing` and `invalid` cannot prove authoring safety.

**[ASSUMED — RECOMMENDATION]** `raw_location_json` is the exact UTF-8 byte slice for the object's `location` member. It is stored redundantly as BLOB because location integrity is the safety invariant, because it permits a direct digest/assertion, and because later edits to other fields must not require parsing and reserializing the location.

**[ASSUMED — RECOMMENDATION]** The ingest parser must operate on `request.get_data()` or the upstream response bytes and retain lexical byte spans. Calling `request.get_json()` and then `json.dumps()` cannot guarantee byte-identical replay because it may change object order, whitespace, Unicode escape spelling, and slash/dot escaping.

**[OBSERVED — STAGE 0 IMPLEMENTATION]** The lexical scanner rejects duplicate JSON object keys before storing any sidecar. In particular, duplicate `updatedAnnotations`, `id`, or `location` members enter the existing logged best-effort capture-failure path. Python's decoder otherwise keeps the last duplicate while a first-match lexical scan can keep different bytes; accepting either representation would invalidate the parsed/raw agreement claim.

**[ASSUMED — RECOMMENDATION]** On output, the serializer emits the stored `raw_location_json` bytes directly as the `location` value. It may rebuild the surrounding annotation object after a valid server edit, but a test must assert that the emitted byte range for `location` is identical to the stored byte range.

**[ASSUMED — RECOMMENDATION]** `raw_annotation_json` is the replay template and forward-compatibility record; the parsed `Annotation` columns remain the query/edit/render representation. A materialization update and its parsed-column update occur in one transaction, and the transaction aborts if the two representations fail invariant checks.

**[ASSUMED — RECOMMENDATION]** `provenance='kobo_patch'` is never sufficient by itself to set `serveable=true`, because PATCH is only a delta. A PATCH materialization becomes serveable only after it is reconciled into an already-authoritative book set or is later confirmed by a cloud seed.

**[ASSUMED — RECOMMENDATION]** `provenance='cwng_authored'` is serveable only for a type and field combination with a proven serializer. Initially that creation allowlist contains server-authored `highlight` with a valid Kobo span and a palette-valid `highlightColor`. Exact seeded `note` rows are representable and do not block book authoring, but creation of a new note still requires the serializer to preserve its observed highlight-shaped text/comment fields. Dogears, unanchored notes, CFI-only highlights, PDF/comic positions, and xpointer positions are not server-created in the first implementation.

### 2.3 Per-book authority state

**[ASSUMED — RECOMMENDATION]** Add:

```text
kobo_annotation_book_state
  id                       INTEGER PRIMARY KEY AUTOINCREMENT
  user_id                  INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE
  book_id                  INTEGER NOT NULL
  content_id               VARCHAR(64) NOT NULL
  authority_status         VARCHAR(24) NOT NULL DEFAULT 'unseeded'
  authority_revision       INTEGER NOT NULL DEFAULT 0
  generation_id            VARCHAR(36) NOT NULL
  set_digest               VARCHAR(64) NULL
  current_etag             TEXT NULL
  etag_kind                VARCHAR(24) NULL
  upstream_seed_etag       TEXT NULL
  opaque_content_status    VARCHAR(16) NOT NULL DEFAULT 'unknown'
  opaque_content_source    VARCHAR(32) NULL
  opaque_content_checked_at DATETIME NULL
  seeded_at                DATETIME NULL
  last_mutation_at         DATETIME NULL
  quarantine_reason        VARCHAR(64) NULL
  updated_at               DATETIME NOT NULL

  UNIQUE(user_id, book_id)
  UNIQUE(user_id, content_id)
  CHECK authority_status IN
    ('unseeded', 'seeding', 'authoritative', 'quarantined', 'disabled')
  CHECK etag_kind IS NULL OR etag_kind IN ('kobo_manifest', 'cwng_revision')
  CHECK opaque_content_status IN ('unknown', 'absent', 'present')
  CHECK opaque_content_source IS NULL OR opaque_content_source IN
    ('device_db_audit', 'wire_attachments', 'wire_attachments_verified')
  INDEX ix_kabs_user_content(user_id, content_id)
  INDEX ix_kabs_authority(user_id, authority_status)
```

**[ASSUMED — RECOMMENDATION]** `generation_id` is a random UUID created with the state row and never reused after a destructive reset/reseed. It prevents an old device ETag from accidentally matching a new authority history whose integer revision restarted.

**[OBSERVED — STAGE 0 IMPLEMENTATION]** `kobo_annotation_book_state.content_id` means the bare Kobo book content ID only after live wire/entitlement evidence binds it. `annotation.content_id` is chapter-scoped (`book!!chapter`) and must never be copied into this field. Legacy migration rows therefore use a bounded `legacy-book:<book_id>` sentinel that is explicitly not a wire lookup key. Stage 1 must resolve the wire content ID to a library `book_id`, fall back to the existing `(user_id, book_id)` row, and atomically replace its sentinel; it must not insert a second row and collide with `UNIQUE(user_id, book_id)`.

**[ASSUMED — RECOMMENDATION]** `authority_revision` advances atomically with every accepted member change or tombstone affecting the served set. `set_digest` is SHA-256 over the ordered, exact outgoing annotation objects and is an integrity check, not the protocol ETag.

**[ASSUMED — RECOMMENDATION]** `authority_status='authoritative'` is a positive assertion that CWNG knows the complete set, including the complete empty set. No other status may result in a successful annotation GET.

**[ASSUMED — RECOMMENDATION]** `opaque_content_status` is a separate per-book authoring gate. `present` means at least one native row is known to carry non-NULL `ExtraAnnotationData` or its wire-equivalent opaque attachment; `absent` means the property was proven absent by an accepted evidence path; `unknown` means it was not proven either way. Only `absent` permits CWNG-created edits, additions, or deletions in the Kobo-served set. Both `unknown` and `present` prohibit Kobo authoring for that book.

**[OBSERVED]** The property gate correctly permits all three observed `note` rows because their `ExtraAnnotationData` is NULL, while it blocks exactly the 49 `markup` rows whose opaque drawing content is non-NULL.

**[ASSUMED — RECOMMENDATION]** The gate is evaluated per `(user_id, book_id)`, never per library and never merely per annotation mutation. One blocked book does not disable two-way annotation authoring for other proven-clear books.

**[ASSUMED — RECOMMENDATION]** One opaque row blocks authoring of the entire Kobo set for that book, including edits or deletions of otherwise ordinary highlights and notes. The implementation must not scope the prohibition only to the markup row.

**[ASSUMED — RECOMMENDATION]** `present` is sticky: ordinary cloud seeds, PATCH deltas, omission from a later response, type changes, or an empty attachments observation cannot downgrade it. A future explicit recovery procedure would require a fresh complete device-database audit plus a matching accepted cloud seed and is outside this first implementation.

**[OBSERVED — STAGE 0 IMPLEMENTATION]** Sticky knowledge is stored independently of the mutable authority row:

```text
kobo_opaque_content_present_guard
  user_id          INTEGER NOT NULL REFERENCES user(id) ON DELETE CASCADE
  book_id          INTEGER NOT NULL
  first_observed_at DATETIME NOT NULL
  PRIMARY KEY(user_id, book_id)
```

**[OBSERVED — STAGE 0 IMPLEMENTATION]** SQLite triggers reject a direct `present -> unknown|absent` update and reject an `unknown|absent` insert whenever this guard exists. Inserts or updates to `present` create the guard. Deleting and reinserting mutable authority state therefore cannot erase the observation, and `INSERT OR REPLACE` cannot bypass it. A complete user/book privacy purge intentionally deletes both authority state and the guard; deleting authority state alone retains the guard.

**[ASSUMED — RECOMMENDATION]** `absent` requires set-level evidence, not a spot check: either a contemporaneous database audit of the same device whose declared ETag exactly matches the accepted cloud seed, with the same annotation IDs and NULL `ExtraAnnotationData` on every row for that book; or, after the carrier experiment succeeds, an accepted complete seed in which every annotation has empty attachments. An audit of a different/stale device or a subset of rows leaves status `unknown`.

### 2.4 Per-device acknowledgment state

**[ASSUMED — RECOMMENDATION]** Add:

```text
kobo_device_book_annotation_state
  id                    INTEGER PRIMARY KEY AUTOINCREMENT
  device_id             INTEGER NOT NULL REFERENCES device(id) ON DELETE CASCADE
  book_state_id         INTEGER NOT NULL
                          REFERENCES kobo_annotation_book_state(id) ON DELETE CASCADE
  last_declared_etag    TEXT NULL
  last_declared_at      DATETIME NULL
  last_served_revision  INTEGER NULL
  last_served_etag      TEXT NULL
  last_ack_revision     INTEGER NULL
  last_ack_at           DATETIME NULL

  UNIQUE(device_id, book_state_id)
  INDEX ix_kdbas_book_ack(book_state_id, last_ack_revision)
```

**[ASSUMED — RECOMMENDATION]** An acknowledgment is inferred only when the same device later declares exactly the ETag CWNG served for that revision. A 200 response alone is delivery, not adoption.

**[ASSUMED — RECOMMENDATION]** Failure to resolve a device identity does not make the set incomplete, but it disables acknowledgment-dependent conflict operations, including accepting an untimestamped deletion. It does not authorize a guessed shared device.

### 2.5 Seed evidence and immutable page snapshots

**[ASSUMED — RECOMMENDATION]** Add an audit record for every seed attempt:

```text
kobo_annotation_seed_capture
  id                  INTEGER PRIMARY KEY AUTOINCREMENT
  book_state_id       INTEGER NOT NULL
                        REFERENCES kobo_annotation_book_state(id) ON DELETE CASCADE
  device_id           INTEGER NULL REFERENCES device(id) ON DELETE SET NULL
  started_at          DATETIME NOT NULL
  completed_at        DATETIME NULL
  device_etag         TEXT NULL
  upstream_etag       TEXT NULL
  response_sha256     VARCHAR(64) NULL
  annotation_count    INTEGER NULL
  page_count          INTEGER NULL
  result              VARCHAR(24) NOT NULL
  failure_reason      VARCHAR(64) NULL

  CHECK result IN ('pending', 'accepted', 'rejected', 'failed')
  INDEX ix_kasc_book_time(book_state_id, started_at)

kobo_annotation_seed_capture_page
  id                  INTEGER PRIMARY KEY AUTOINCREMENT
  seed_capture_id     INTEGER NOT NULL
                        REFERENCES kobo_annotation_seed_capture(id) ON DELETE CASCADE
  page_number         INTEGER NOT NULL
  request_offset_token TEXT NULL
  response_body_gzip  BLOB NOT NULL
  response_sha256     VARCHAR(64) NOT NULL
  response_etag       TEXT NULL
  next_offset_token   TEXT NULL

  UNIQUE(seed_capture_id, page_number)
  INDEX ix_kascp_capture(seed_capture_id, page_number)
```

**[ASSUMED — RECOMMENDATION]** An accepted seed retains every exact upstream page body separately, compressed, together with its request token, response ETag, next token, and digest. The parent `response_sha256` covers the ordered page digests. This makes a paginated baseline unambiguous and lets an operator prove or restore it even if parsed rows or normal rolling backups are later damaged.

**[ASSUMED — RECOMMENDATION]** Add immutable pagination snapshots:

```text
kobo_annotation_page_snapshot
  snapshot_id         VARCHAR(64) PRIMARY KEY
  book_state_id       INTEGER NOT NULL
                        REFERENCES kobo_annotation_book_state(id) ON DELETE CASCADE
  device_id           INTEGER NULL REFERENCES device(id) ON DELETE CASCADE
  authority_revision  INTEGER NOT NULL
  etag                TEXT NOT NULL
  ordered_payload_gzip BLOB NOT NULL
  annotation_count    INTEGER NOT NULL
  page_size           INTEGER NOT NULL
  created_at          DATETIME NOT NULL
  expires_at          DATETIME NOT NULL

  INDEX ix_kaps_expiry(expires_at)

kobo_annotation_page_cursor
  token               VARCHAR(64) PRIMARY KEY
  snapshot_id         VARCHAR(64) NOT NULL
                        REFERENCES kobo_annotation_page_snapshot(snapshot_id)
                        ON DELETE CASCADE
  page_offset         INTEGER NOT NULL
  created_at          DATETIME NOT NULL

  UNIQUE(snapshot_id, page_offset)
  INDEX ix_kapc_snapshot(snapshot_id)
```

**[ASSUMED — RECOMMENDATION]** `snapshot_id` and every cursor `token` are cryptographically random 256-bit URL-safe values. A `pageOffsetToken` is only the random cursor token; it carries no offset or trusted state client-side. The server resolves it to one immutable snapshot and offset under the authenticated user, content ID, and device when available.

### 2.6 Conflict audit

**[ASSUMED — RECOMMENDATION]** Add an append-only mutation audit:

```text
annotation_revision
  id                   INTEGER PRIMARY KEY AUTOINCREMENT
  annotation_id        INTEGER NOT NULL REFERENCES annotation(id) ON DELETE CASCADE
  audit_sequence       INTEGER NOT NULL
  resulting_content_revision INTEGER NULL
  book_authority_revision INTEGER NULL
  mutation_kind        VARCHAR(24) NOT NULL
  actor_device_id      INTEGER NULL REFERENCES device(id) ON DELETE SET NULL
  source               VARCHAR(16) NOT NULL
  client_modified_at   DATETIME NULL
  raw_client_time      TEXT NULL
  server_received_at   DATETIME NOT NULL
  payload_digest       VARCHAR(64) NOT NULL
  accepted             BOOLEAN NOT NULL
  rejection_reason     VARCHAR(64) NULL
  changed_fields_json  TEXT NOT NULL

  UNIQUE(annotation_id, audit_sequence)
  INDEX ix_ar_annotation_time(annotation_id, server_received_at)
```

**[ASSUMED — RECOMMENDATION]** `audit_sequence` advances for every candidate, accepted or rejected. `resulting_content_revision` is the new live `Annotation.content_revision` for an accepted mutation and NULL for a rejected candidate. Rejected candidates therefore remain auditable without advancing live content.

## 3. Migration and partial-migration behavior

**[ASSUMED — RECOMMENDATION]** Implement one `migrate_kobo_two_way_annotation_sync(engine, session)` immediately after the existing annotation/device migrations in `migrate_Database()`.

**[ASSUMED — RECOMMENDATION]** The migration follows the existing convention: `Base.metadata.create_all(..., checkfirst=True)` for new tables, one `engine.begin()` transaction for annotation-column additions and backfills, live `PRAGMA table_info(annotation)`, per-column duplicate recovery, and `CREATE INDEX IF NOT EXISTS`.

**[ASSUMED — RECOMMENDATION]** Extend the existing earlier `migrate_user_table()` and `migrate_config_table()` paths to add `user.kobo_two_way_annotation_sync BOOLEAN NOT NULL DEFAULT 0` and `settings.config_kobo_two_way_annotation_sync BOOLEAN NOT NULL DEFAULT 0`. Backfill every pre-existing row to `0`; a NULL value is evaluated as off. The settings flag is the per-Calibre-library/instance gate because this deployment model stores one active library configuration in `settings`.

**[ASSUMED — RECOMMENDATION]** Fresh databases receive the full model through `create_all`. Upgrades add the four annotation columns and tables idempotently.

**[ASSUMED — RECOMMENDATION]** Backfill existing `annotation.content_revision` to `1` and `server_modified_at` to `COALESCE(last_synced, created_at)`. This preserves a baseline ordering without relabeling a server time as a client time.

**[ASSUMED — RECOMMENDATION]** Do not infer `annotation_type` for historical rows. A nonempty `highlighted_text` suggests a highlight but does not prove whether a note variant or other native shape produced it; an empty string could be a dogear or damaged/legacy data.

**[ASSUMED — RECOMMENDATION]** Do not backfill `kobo_annotation_materialization` from parsed path columns. Reconstructing JSON would lose the observed escape spelling, optional-field presence, property order, `attachments`, and exact timestamp spelling.

**[OBSERVED — STAGE 0 IMPLEMENTATION]** Insert one `kobo_annotation_book_state` for every distinct existing non-NULL `(user_id, book_id)` whose user still exists, with status `unseeded`, revision `0`, no digest, no ETag, `opaque_content_status='unknown'`, and a non-wire `legacy-book:<book_id>` content sentinel. NULL-key and deleted-user groups are left unchanged and logged by count. Existing annotations remain fully usable in the web UI but are not declared a complete Kobo set or safe for authoring.

**[ASSUMED — RECOMMENDATION]** Leave `last_editor_device_id` NULL for historical rows. `source='kobo'` identifies an ingest protocol, not a specific physical actor.

**[ASSUMED — RECOMMENDATION]** Before any two-way implementation is enabled, increment the annotation backup format to schema version 3 and include every generic annotation column plus the Kobo materialization and book-authority metadata. Add a synchronous pre-replacement backup that records empty sets; the current asynchronous rolling backup is supplementary, not the transaction precondition.

**[OBSERVED — STAGE 0 IMPLEMENTATION]** A schema-capability check runs before route ownership logic. If any required table, column, index, sticky-evidence table, or sticky trigger is missing, the instance-wide two-way feature evaluates false and owned books stay behind the current `checkforchanges -> []` containment behavior. Fresh `create_all`, upgrade migration, and the end of the full startup migration sequence all install/heal the triggers so a later table rebuild cannot leave the same boot certified as capable without them.

**[OBSERVED — STAGE 0 IMPLEMENTATION]** Startup raises for a Stage 0 DDL failure, an annotation row-count change, a newly created Stage 0 foreign-key violation, or a newly inserted authority row that is not `unseeded/unknown`. If an older binary is rolled back over the additive schema, it ignores the new tables/columns and continues to use the existing annotation paths; it must not drop them automatically.

**[OBSERVED — STAGE 0 IMPLEMENTATION]** Production migration postconditions are deliberately provenance-scoped:

- Annotation row count is fatal if changed. Per-value identity for text, notes, colors, content IDs, parsed locations, CFI/xpointer/PDF/comic positions, hidden/source/device fields is proven by migration tests and production-snapshot dry runs, not by duplicating every annotation value into memory during each startup. The migration updates only newly added columns.
- Every eligible legacy group is expected to have one state. Failure of a new insert is fatal; a surviving non-one cardinality can only come from pre-existing partial/non-canonical state and is count-logged while gates remain fail-closed. NULL-key and deleted-user groups are also count-logged and skipped rather than making an unrelated historical row prevent startup.
- Foreign-key checks are fatal only for new violations in Stage 0-owned tables or `annotation.last_editor_device_id`. The migration records the scoped baseline before DDL/backfill and compares afterward. Database-wide historical orphans are count-logged because long-lived SQLite installations commonly accumulated them with FK enforcement off; Stage 0 must not claim or block startup on violations it did not create.

**[OBSERVED — STAGE 0 IMPLEMENTATION]** User/book purge is part of the schema lifecycle. It deletes raw materializations before annotation IDs can recycle, then device acknowledgment, seed-page, seed-capture, cursor, snapshot, authority, and sticky-guard rows in child-first order. Immutable seed/snapshot children are deleted because they can contain annotation payloads and have no valid ownership or restore meaning after their user/book scope is erased. ORM parent relationships mirror those cascades for ordinary non-bulk deletes; the explicit purge remains authoritative because app-wide SQLite FK enforcement and bulk cascades cannot be assumed.

## 4. Verbatim location and web-reader coexistence

**[OBSERVED]** Kobo accepted and returned both `span#kobo\\.3\\.4` and a full chapter path ending in `#kobo.194.6` in the same annotation set.

**[ASSUMED — RECOMMENDATION]** The raw Kobo location is the native replay authority. `start_container_path`, offsets, `content_id`, `chapter_progress`, and `cfi_range` are projections for CWNG readers and exports.

**[ASSUMED — RECOMMENDATION]** Ingest order is: capture exact raw object/location bytes; validate bounded JSON and required fields; persist the raw materialization; parse projections without mutating the raw bytes; compute/retain `cfi_range` through the existing web-reader path; and commit both forms atomically.

**[ASSUMED — RECOMMENDATION]** Project the observed top-level `context` into `Annotation.context_string`; retain optional `chapterTitle` and `attachments` only in the exact Kobo materialization until a cross-reader consumer has a justified typed column. This corrects the current top-level-context gap without changing the web-reader contract.

**[OBSERVED]** On the Libra Colour, the decisive native property is `ExtraAnnotationData`, not `Bookmark.Type`: all 49 and only the 49 markup rows have a non-NULL value; all three note rows have NULL. The drawing payload is opaque to CWNG.

**[ASSUMED]** A nonempty wire `attachments` object is the plausible carrier for `ExtraAnnotationData`; the Clara wire sample contained only `{}`, so the relationship is not yet observed.

**[ASSUMED — RECOMMENDATION]** Merge these facts into one fail-closed rule. Any device-database evidence of non-NULL `ExtraAnnotationData`, or any wire annotation with nonempty `attachments`, sets the book's `opaque_content_status='present'`. Empty wire attachments do not set `absent` until the carrier relationship is proven; they leave the property `unknown` unless an independent device-database audit proves every row NULL.

**[ASSUMED — RECOMMENDATION]** Before the carrier experiment closes, a nonempty-attachments seed is captured but cannot become authoritative because exact markup replay is unverified. After exact replay is proven, such a seed may support unchanged authoritative replay, but `opaque_content_status='present'` permanently prohibits CWNG authoring for that book.

**[ASSUMED — RECOMMENDATION]** A projection failure degrades only that projection. For example, an unusable `chapterFilename` may leave `content_id` unchanged/NULL, but it must not discard or rewrite the raw location or the annotation.

**[ASSUMED — RECOMMENDATION]** Web rendering continues to prefer `cfi_range` where it does today and may derive a CFI from parsed Kobo spans through the existing path. No code should feed `raw_location_json` directly to epub.js.

**[ASSUMED — RECOMMENDATION]** A web edit to note or color updates both the generic columns and the raw-object replay template while splicing the stored raw location unchanged. A web edit never normalizes, rebuilds, or relocates the native span.

**[OBSERVED]** Kobo's complete highlight palette round-trips as `#F6F3B3 -> Color 0` (yellow), `#E8AFCF -> Color 1` (pink), `#B2E1E8 -> Color 2` (blue), `#C6E09E -> Color 3` (green), and `#A0A0A0 -> Color 4` (grey).

**[OBSERVED]** A greyscale Clara BW accepts and stores all five palette values, including the four non-grey color indices; color fidelity is not gated by display capability.

**[OBSERVED]** `#FF0000` is outside Kobo's palette and is silently coerced. The earlier conclusion that it mapped equivalently to `#A0A0A0` was a measurement of rejection, not a valid color mapping.

**[ASSUMED — RECOMMENDATION]** Every Kobo materialization with `highlightColor` must contain exactly one of the five observed palette strings. CWNG maps web color names only to those exact hex values, emits the selected value byte-for-byte, and rejects/quarantines rather than inventing or forwarding an out-of-palette authored value. Dogears remain colorless as observed.

**[ASSUMED — RECOMMENDATION]** A CFI-only or unanchored web annotation remains a valid CWNG annotation but has no serveable Kobo materialization. It is excluded from Kobo GET and does not make the Kobo authoritative set incomplete because it has never been claimed as a Kobo-set member.

**[ASSUMED — RECOMMENDATION]** A web-created KoboSpan highlight may enter the Kobo set only through the hardware-proven authoring serializer and only after the book is authoritative. Its server-generated UUID becomes the stable Kobo annotation ID.

## 5. One-time cloud seed

### 5.1 Trigger and atomic algorithm

**[ASSUMED — RECOMMENDATION]** Seeding begins only when the instance gate and user opt-in are enabled, an owned book is `unseeded`, and an authenticated Kobo request supplies live Reading Services credentials plus a resolvable device identity.

**[ASSUMED — RECOMMENDATION]** The server changes the book state to `seeding` with a compare-and-swap so only one seed runs. Other requests for that book receive `[]` from `checkforchanges` and no local GET while the seed is in progress.

**[ASSUMED — RECOMMENDATION]** After proxying the device's PATCH successfully, the server fetches the complete upstream Kobo annotations collection using the same authenticated request context. It follows upstream pagination to exhaustion, preserves every response body page and ETag, and enforces bounded page/byte/annotation limits.

**[ASSUMED]** Fetching the full collection server-side with the current authenticated headers is expected to be equivalent to transparently proxying the device's GET, but this exact server-initiated use of credentials has not yet been measured.

**[ASSUMED — RECOMMENDATION]** The seed is accepted only when all pages are valid, IDs are unique, every object has a bounded replay-safe shape, every `highlight` has exactly one of the five observed `highlightColor` values, every dogear omits color, any other seeded type preserves color presence/value exactly, the final union has a stable upstream ETag, and the upstream ETag exactly equals the device ETag declared in the immediately following `checkforchanges` request.

**[OBSERVED]** The device `checkforchanges` ETag and the GET response ETag carried the same composite manifest in the measured exchange.

**[ASSUMED — RECOMMENDATION]** Exact ETag equality is the proof that the cloud collection and that device declare the same set/version. A count comparison alone is insufficient because two different sets can have the same count.

**[ASSUMED — RECOMMENDATION]** Before transition to `authoritative`, render the complete candidate GET from the stored materializations and compare every source/output `highlightColor` for exact string equality. Reparse the candidate response and require the same annotation ID-to-color map, including color absence on dogears. A missing, changed, out-of-palette, added, or removed color rejects the seed; ETag equality alone is insufficient.

**[ASSUMED — RECOMMENDATION]** Also evaluate opaque-content evidence before the transition. Any nonempty `attachments` sets the per-book property to `present` and, until the attachments/`ExtraAnnotationData` carrier and exact replay are hardware-proven, prevents the seed from becoming authoritative. All-empty attachments leave the authoring gate `unknown` until an accepted evidence path proves absence.

**[ASSUMED — RECOMMENDATION]** Before replacing or hiding any local row, write and fsync the compressed exact seed capture, verify its SHA-256 by reading it back, and synchronously snapshot the current local set. If either safety artifact fails, reject the seed without changing authority or annotations.

**[ASSUMED — RECOMMENDATION]** Reconcile the accepted seed in one DB transaction: insert missing annotations; update matching IDs and exact materializations; leave non-Kobo-only CWNG annotations intact but outside the Kobo materialized set; and mark previously authoritative Kobo members omitted by a later complete set as hidden tombstones. Initial seeding has stricter rules for pre-existing unmatched Kobo rows, described below.

**[ASSUMED — RECOMMENDATION]** On successful initial reconciliation, set status `authoritative`, revision `1`, `etag_kind='kobo_manifest'`, `current_etag=upstream ETag`, the ordered set digest, and seed time atomically; mark the corresponding capture row `accepted` in the same transaction. Return `[]` for the triggering check because the device already declared the exact accepted manifest.

### 5.2 Kobo unavailable or malformed

**[ASSUMED — RECOMMENDATION]** A timeout, 429, 5xx, authentication failure, invalid JSON, inconsistent ETags across pages, duplicate IDs, missing page, exceeded bound, or unknown annotation shape rejects the seed. The state returns to `unseeded` or enters `quarantined` with a non-sensitive reason; the device receives `[]` from CWNG's owned-book `checkforchanges` path.

**[ASSUMED — RECOMMENDATION]** PATCH continues to proxy upstream and is captured locally even when seeding fails. Its delta improves durability but never upgrades completeness.

**[ASSUMED — RECOMMENDATION]** Retry seeding on a later open/close with exponential per-book backoff. Never retry in a tight loop and never substitute a locally reconstructed set.

### 5.3 Device rows absent from Kobo cloud

**[ASSUMED — RECOMMENDATION]** If the device-declared ETag differs from the completed upstream GET ETag, treat the cloud seed as not matching the device and do not serve it. Preserve all device PATCH captures and local rows, set `quarantined`, and continue returning `[]` from `checkforchanges`.

**[ASSUMED — RECOMMENDATION]** This covers a device holding rows Kobo does not: CWNG cannot recover unseen rows from delta PATCH, so omission is not a solvable conflict. The safe recovery paths are a later device upload that makes manifests equal, a newly designed explicit device-database import, or user-reviewed conflict resolution. The server must not guess.

**[ASSUMED — RECOMMENDATION]** If a pre-existing `source='kobo'` local row is absent from the candidate cloud seed, reject initial seeding even if it is hidden. The row may be evidence of device-only state, and initial authority must not erase that evidence.

**[ASSUMED — RECOMMENDATION]** A pre-existing web-only row without a Kobo materialization does not block seeding because it is not claimed to exist in the Kobo native set. A pre-existing `cwng_authored` Kobo materialization absent upstream does block seeding until the authoring workflow explicitly merges it after the cloud baseline is accepted.

## 6. Serving `GET /api/v3/content/<id>/annotations`

### 6.1 Eligibility

**[ASSUMED — RECOMMENDATION]** Serve a CWNG-authored 200 annotation set only when: the instance kill switch is on; the user opt-in is on; ownership resolves positively; the exact `(user, book)` state exists and is `authoritative`; every visible native member has a serveable materialization; no unresolved conflict/quarantine exists; and the requested pagination token, if any, is valid.

**[ASSUMED — RECOMMENDATION]** Kobo authoring has one additional mandatory check: `opaque_content_status` must be `absent` for that exact book. `unknown` and `present` allow no CWNG-created annotation, native-row edit, or deletion to enter the Kobo-served set. They may still allow byte-exact unchanged replay once its separate authority checks have passed. A separate CFI-only/unanchored web annotation may continue to exist outside the Kobo set as described in §4; it cannot advance the Kobo book revision or be presented to the device.

**[ASSUMED — RECOMMENDATION]** If any eligibility check fails before `checkforchanges`, return `[]` there so the device never issues GET. This trigger boundary is the fail-safe mechanism; no HTTP status after Nickel has begun an authoritative GET is proven to preserve local rows.

**[OBSERVED]** Earlier containment attempts that answered in the GET path did not prevent highlight loss. The new measurement proves that `checkforchanges -> []` prevents GET and touches nothing.

**[ASSUMED — RECOMMENDATION]** An unexpected direct GET for an unseeded owned book must transparently proxy Kobo's response while capturing it as seed evidence when live upstream access succeeds, because Kobo is the measured complete source. It must never substitute CWNG's partial local set. If upstream itself fails, forward the upstream failure unchanged, quarantine the book, and treat device preservation as unknown rather than calling the failure safe.

### 6.2 Page size, ordering, and snapshot isolation

**[OBSERVED]** The measured device requested `limit=100` and immediately followed `nextPageOffsetToken` until it became `null`.

**[ASSUMED — RECOMMENDATION]** Use a server maximum and default page size of 100. If `limit` is present, require an integer from 1 through 100 and store that exact page size in the snapshot; the measured request therefore uses 100. Reject zero, negative, non-integer, or excessive values with a non-success response rather than silently changing set semantics.

**[ASSUMED — RECOMMENDATION]** Stable set ordering is binary ascending `annotation.annotation_id`, with internal row ID only as an impossible-duplicate diagnostic tie-break. The uniqueness constraint on `(user_id, book_id, annotation_id)` must make the tie-break unreachable.

**[ASSUMED — RECOMMENDATION]** On page 1, read the entire eligible ordered set and authority revision in one DB snapshot, render every exact outgoing object, compute/verify its digest, gzip the complete ordered JSON array into `kobo_annotation_page_snapshot`, then return rows 0–99.

**[ASSUMED — RECOMMENDATION]** `nextPageOffsetToken` is `null` when no rows remain. Otherwise create-or-read the unique `kobo_annotation_page_cursor` for the next offset and return only its random token. A retry of the same page returns the same next token. The device never supplies a SQL offset and cannot skip rows by editing a token.

**[ASSUMED — RECOMMENDATION]** Continuation pages read only the immutable snapshot payload, not live `Annotation` rows. A concurrent create/edit/delete advances the live revision but cannot change the in-flight page sequence.

**[ASSUMED — RECOMMENDATION]** All pages carry the same snapshot ETag. After the old snapshot finishes, the next `checkforchanges` sees that the device declares the old ETag and names the book again, delivering the newer complete revision.

**[ASSUMED — RECOMMENDATION]** Snapshots remain idempotently readable for 24 hours, with expiry extended on every valid page read, so ordinary device retries cannot skip or duplicate a page. An expired/unknown/wrong-user/wrong-book token must not restart against a different live revision or serve a partial set. It is an invariant breach: quarantine the book, return a non-success response, and record that device preservation after this point is unproven.

**[ASSUMED — RECOMMENDATION]** Background cleanup deletes only expired snapshot rows. Snapshot expiry is cache cleanup, not annotation deletion.

## 7. `checkforchanges` and ETag strategy

### 7.1 Measured contract

**[OBSERVED]** The request is a JSON array of objects containing `ContentId` and `etag` in the measured single-book exchange.

**[OBSERVED]** The observed ETag is a composite comma-separated manifest with one independently versioned entry per annotation, shaped like `<LETTER>:<version-int>-<stable-id-hash>`.

**[OBSERVED]** The response is a bare JSON array of content-ID strings. `[]` prevented GET; `[content-id]` caused GET.

### 7.2 Options

**[ASSUMED]** Option A is to synthesize Kobo's composite manifest. It could preserve the observed shape and independently advance member versions, but the letter mapping, stable-ID hash algorithm, deletion representation, ordering rule, empty-set representation, and collision expectations are not known. A lookalike manifest would claim protocol knowledge CWNG does not have.

**[ASSUMED]** Option B is a CWNG-owned per-book revision ETag. CWNG does not need to understand the incoming manifest: exact equality with the last ETag it served means unchanged, and inequality means changed. The remaining unknown is whether Nickel accepts and later echoes an arbitrary valid HTTP ETag rather than requiring Kobo's composite grammar.

**[ASSUMED — RECOMMENDATION]** Choose Option B after a hardware gate. Do not synthesize fake Kobo manifest entries.

**[ASSUMED — RECOMMENDATION]** Format the server ETag as `W/"CWNG:<generation-id>:<authority-revision>:<digest-prefix>"`. Store the exact header string in `current_etag`; comparisons are byte-for-byte, including weakness marker and quoting.

**[ASSUMED — RECOMMENDATION]** Before arbitrary ETags pass the hardware experiment, an unchanged seeded set may safely replay its captured `kobo_manifest` ETag. The first server-side mutation must not be offered to a device while ETag compatibility remains unknown; place the book in a non-serving pending/quarantined state and keep `checkforchanges` at `[]`.

### 7.3 Owned-book decision algorithm

**[ASSUMED — RECOMMENDATION]** Parse and validate the complete request array first. For each entry in request order:

1. **[ASSUMED — RECOMMENDATION]** If ownership is definitively not CWNG's, include it in one upstream batch and merge only Kobo's returned IDs.
2. **[ASSUMED — RECOMMENDATION]** If ownership is unknown because lookup failed, suppress it locally and log/metric the uncertainty; a missed pull is safer than a destructive false positive.
3. **[ASSUMED — RECOMMENDATION]** If owned but any feature/authority gate is closed, suppress it and optionally start a safe seed; do not name it in the response.
4. **[ASSUMED — RECOMMENDATION]** If owned and authoritative, record the declaring device/ETag. If the incoming ETag exactly equals `current_etag`, return no ID and advance that device's acknowledgment. Otherwise return the content ID exactly once.

**[ASSUMED — RECOMMENDATION]** The final response is a bare JSON array of strings in original request order, deduplicated. Do not emit object wrappers even though current compatibility parsing accepts upstream object variants.

**[ASSUMED — RECOMMENDATION]** Batch handling must be per entry. No failure, quarantine, or ETag match for one book may suppress or name another book.

## 8. Conflict resolution

### 8.1 Accepted ordering

**[OBSERVED]** Kobo supplies `clientLastModifiedUtc` with subsecond UTC precision in the measured objects, and the existing server stores its parsed value in `client_modified_at`.

**[OBSERVED]** The existing ingester ignores older valid timestamps, treats identical timestamp plus byte-equivalent content as a retry, and uses arrival order for divergent equal-time payloads.

**[ASSUMED — RECOMMENDATION]** Replace arrival-order ties with deterministic, audited last-write-wins over the whole annotation object:

1. **[ASSUMED — RECOMMENDATION]** Same annotation ID, actor, client timestamp, and payload digest is an idempotent retry.
2. **[ASSUMED — RECOMMENDATION]** A greater valid `client_modified_at` wins.
3. **[ASSUMED — RECOMMENDATION]** Web actions use the server commit time as their trusted client time and update `client_modified_at`, `server_modified_at`, `last_editor_device_id`, and revisions atomically.
4. **[ASSUMED — RECOMMENDATION]** Equal valid timestamps with different payloads use `(actor device public_id, payload_digest)` as a stable tie-break and record the loser.
5. **[ASSUMED — RECOMMENDATION]** A missing, malformed, or implausibly future device timestamp may create a previously unknown annotation, but it may not overwrite a divergent established annotation. Quarantine the conflict and suppress destructive GET until reviewed or superseded by a valid later mutation.

**[ASSUMED]** Kobo device clocks may be manually wrong; their reliability under clock changes has not been measured. The implementation should make the future-skew threshold configurable and initially conservative rather than silently clamping a value into a false order.

**[ASSUMED — RECOMMENDATION]** Treat native location as immutable for ordinary note/color edits. If a device sends the same ID with a changed location, treat it as a whole-object mutation; accept only under the timestamp rule and preserve the exact new raw location. Record `location` among changed fields.

### 8.2 Offline edits and deletions

**[ASSUMED — RECOMMENDATION]** If a device edits offline while the web edits the same annotation, the later valid `clientLastModifiedUtc` wins. The losing candidate stays in the revision audit, and the next authoritative GET converges the device to the winner.

**[ASSUMED — RECOMMENDATION]** If the offline device's clock is invalid or the conflict cannot be ordered safely, do not serve either guessed resolution. Keep the last accepted row, quarantine the book, retain the incoming candidate evidence, and return `[]` from `checkforchanges`.

**[OBSERVED]** The current deletion PATCH shape supplies annotation IDs but no per-deletion timestamp.

**[ASSUMED — RECOMMENDATION]** Accept an untimestamped device deletion only when that device's last acknowledged book revision equals the current authority revision before the delete. Otherwise the deletion may be based on stale offline state and becomes a quarantined conflict.

**[ASSUMED — RECOMMENDATION]** A web deletion is ordered by trusted server action time, sets `hidden=true`, advances annotation/book revisions, and retains the exact materialization for rollback. Hidden rows are omitted from GET only after the mutation is accepted.

**[ASSUMED — RECOMMENDATION]** Conflict resolution is whole-object LWW, not a field-level merge. The measured PATCH establishes annotation-level deltas but does not establish which members within an annotation object are intentional deltas; merging note/color/location independently could invent a state no client authored.

## 9. Kill switch, opt-in, and fail-safe behavior

### 9.1 Gates

**[ASSUMED — RECOMMENDATION]** Add `settings.config_kobo_two_way_annotation_sync BOOLEAN NOT NULL DEFAULT 0` as the instance/library kill switch.

**[ASSUMED — RECOMMENDATION]** Add `user.kobo_two_way_annotation_sync BOOLEAN NOT NULL DEFAULT 0` as the per-user opt-in. Existing and new users both default off because this feature can delete native rows by omission.

**[OBSERVED — STAGE 0 IMPLEMENTATION]** The rendered user checkbox submits a companion presence marker. Profile/admin save paths mutate the opt-in only when that marker is present: rendered+checked enables, rendered+unchecked disables, and a form where Kobo controls were not rendered preserves the stored value. This avoids a hidden control silently resetting an opt-in and is required before Stage 1 consumes the flag.

**[ASSUMED — RECOMMENDATION]** Both flags must be true and the book state must be authoritative. Disabling either flag immediately stops naming owned books in `checkforchanges`; it does not delete authority data, annotations, seed captures, or backups.

**[ASSUMED — RECOMMENDATION]** A per-book opaque-content quarantine affects only annotation authoring/replacement for that book. It does not block the preceding `PUT /kobo/<token>/v1/library/<uuid>/state`, reading-position persistence, library sync, downloads, shelves, or annotation sync for other books.

**[ASSUMED — RECOMMENDATION]** A process environment emergency override, `CWNG_KOBO_TWO_WAY_ANNOTATIONS=0`, may force the feature off but may never force it on. This gives an operator a restart-level kill switch even if the settings DB/UI is unavailable.

### 9.2 Safe failure matrix

| Condition | **[ASSUMED — RECOMMENDATION]** Behavior |
|---|---|
| Feature off, user not opted in, unseeded, seeding, quarantined, schema incomplete | Owned-book `checkforchanges` omits the ID (`[]` if it was the only entry); no GET is induced. |
| Direct GET while not authoritative | Transparently proxy and capture Kobo's measured-complete set when upstream succeeds; never substitute local partial data. If upstream fails, forward that failure and mark preservation unknown. |
| Authoritative known-empty set | 200 with `{"annotations":[],"nextPageOffsetToken":null}` and current ETag. |
| DB/ownership lookup error | Suppress owned/unknown ID at trigger boundary; record telemetry; never proxy a potentially destructive success. |
| Snapshot/token error | Non-success plus immediate quarantine; never switch to live rows mid-pagination, and do not claim that the device preserved its rows. |
| PATCH capture failure but upstream PATCH can proceed | Proxy upstream, do not advance local authority, quarantine if an authoritative set may now differ. |
| Upstream PATCH failure | Preserve local evidence, do not claim convergence, quarantine the book. |
| Backup/seed artifact failure | Abort mutation/seed; remain non-authoritative. |
| Unfamiliar bounded native type with valid exact materialization | Preserve and replay exactly; type alone does not block the book or imply opaque content. |
| Malformed, unbounded, or invalid native object | Preserve bounded evidence where possible, mark unserveable, and quarantine the annotation book before any GET. |
| Any member lacks a valid serveable materialization | Do not serve the book at all; never silently omit that member. |
| `opaque_content_status='unknown'` | Block create/edit/delete authoring of that book's Kobo set; continue reading-position sync, web-only annotations outside the set, and any separately proven unchanged replay. |
| `opaque_content_status='present'` | Permanently block create/edit/delete authoring of that book's Kobo set; preserve opaque rows exactly; continue reading-position sync, web-only annotations outside the set, and other books. |
| Seed color is missing, changed, or outside the five-value palette during candidate replay | Reject authority transition and quarantine the annotation book; never coerce the color. |

**[ASSUMED — RECOMMENDATION]** Metrics and logs distinguish `suppressed_not_authoritative`, `suppressed_quarantined`, `authoring_blocked_opaque_unknown`, `authoring_blocked_opaque_present`, `seed_color_mismatch`, `seed_failed`, `direct_get_refused`, `snapshot_expired`, `etag_mismatch`, and `conflict_unordered`. Logs must not include auth headers, user keys, full annotation text, notes, opaque drawing data, or raw payloads.

## 10. Staged rollout and verification gates

### Stage 0 — schema and passive capture

**[ASSUMED — RECOMMENDATION]** Ship additive schema, backup schema 3, raw lexical capture, exact projection invariants, authority state, metrics, and UI controls with both gates forced off. Continue current safe owned-book trigger suppression.

**[ASSUMED — RECOMMENDATION]** Test migration on fresh, legacy, partially created, and repeated-run databases. Prove generic annotation values and `cfi_range` behavior are unchanged.

### Stage 1 — seed-only observe mode

**[ASSUMED — RECOMMENDATION]** Permit opted-in users to capture cloud seeds, but do not name books or serve GET. Compare device manifest to seed ETag over repeated open/close cycles and multiple books/devices; run the exact candidate color comparison; and record each book's opaque-content state independently. Surface seed, color, opaque-content, and quarantine state to the user.

### Stage 2 — unchanged-set replay

**[ASSUMED — RECOMMENDATION]** For a small explicit cohort, replay only the exact accepted seed with its original Kobo composite ETag. No server creates, edits, or deletes are eligible. Verify local DB count, IDs, text, location strings, exact `highlightColor` per ID, color absence on dogears, and untouched non-target books before/after.

### Stage 3 — CWNG ETag experiment

**[ASSUMED — RECOMMENDATION]** On a sacrificial test book with a verified device DB backup, serve an unchanged set under a `CWNG` revision ETag, close/open twice, and verify the next `checkforchanges` echoes it exactly. Then make one server-side addition and verify the device requests/adopts the next revision without drift.

**[ASSUMED — RECOMMENDATION]** Failure to echo arbitrary ETags blocks server mutations. Keep seed replay only; do not fall back to invented composite manifests.

### Stage 4 — create, then edit, then delete

**[ASSUMED — RECOMMENDATION]** Enable one operation at a time only on books whose opaque-content status is proven `absent`: server-created highlight, edits through each of the five observed palette colors, representable note/comment mutation through its exact serializer, and deletion last. For every operation, trace OBSERVED links from web commit, authority revision, check response, GET pages, device DB adoption, next ETag acknowledgment, and no changes to unrelated books.

### Stage 5 — wider opt-in

**[ASSUMED — RECOMMENDATION]** Expand only after multi-device, offline-edit, pagination-over-100, upstream-outage, restart-mid-seed, restart-mid-page, and rollback tests pass. Keep both defaults off until a separate product decision explicitly changes them.

**[ASSUMED — RECOMMENDATION]** Every rollout stage has an immediate rollback path: turn off the instance flag. Rollback stops future destructive responses without rewriting any annotation data.

## 11. Remaining unknowns and experiments required

**[OBSERVED]** Native `note` representability and all five `highlightColor` mappings are closed hardware results and are intentionally absent from this remaining-unknowns list.

### 11.1 Batched `checkforchanges`

**[OBSERVED]** No measured `checkforchanges` request contained more than one content ID.

**[OBSERVED 2026-08-23]** Still true with request bodies finally captured. Every captured
`checkforchanges` carried exactly **one** entry, shaped `{"ContentId": …, "etag": …}`, and the
device only asked about a book it had **just had open** — books it was not actively reading were
absent from the request entirely. So the batching question is not merely unmeasured, it may be
hard to provoke: to see a multi-entry request you likely have to make several books active in one
session. The `etag` the device sends is exactly the token it has stored for that book, which is
what makes the section 7.3 equality test workable.

**[OBSERVED 2026-08-23]** The annotations GET the trigger produces is
`GET /api/v3/content/<id>/annotations?limit=100` — the device asks for a page size of 100, which
is the concrete number section 11.4's pagination work has to satisfy.

**[ASSUMED — RECOMMENDATION]** Implement array parsing and independent per-entry decisions now, but gate production claims about batch ordering/partial upstream merge until tested.

**[ASSUMED — RECOMMENDATION]** Closing experiment: open/close or force sync with at least two changed books, capture request/response order, and test a mixed batch containing one CWNG-authoritative owned book, one unseeded owned book, and one non-owned Kobo book.

### 11.2 Arbitrary ETag grammar — **CLOSED 2026-08-23. Nickel treats the ETag as opaque.**

**[OBSERVED]** Kobo's emitted ETag is a structured composite manifest, not an opaque random token.

**[OBSERVED 2026-08-23 — the Stage 3 experiment was run on hardware and it passed.]** Nickel
accepts a CWNG-authored ETag that shares none of Kobo's manifest grammar, stores it byte-for-byte,
and sends it back on the next `checkforchanges`.

Instrument: the arming-file-gated scratch probe, hard-scoped to one ContentId, on the deployed
server, against the operator's Clara BW. Subject: a book that was **already downloaded, had zero
annotations, and carried the measured empty-set token `W/"0"`** — chosen so that no annotation
could be lost whichever way the experiment went. The served body was the book's true state,
`{"annotations":[],"nextPageOffsetToken":null}`, so the device's set could not change.

    Cycle A  device -> checkforchanges  [{ContentId: 053742ff…, etag: W/"0"}]
             server -> names the book so Nickel issues its GET
             device -> GET /api/v3/content/053742ff…/annotations?limit=100
             server -> 200, 45 bytes, ETag W/"CWNG:63e653e9-…:1:7be828578aae0f01"
             device DB AnnotationsSyncToken:  W/"0"  ->  W/"CWNG:63e653e9-…:1:7be828578aae0f01"
                                              ADOPTED BYTE-FOR-BYTE

    Cycle B  device -> checkforchanges  [{ContentId: 053742ff…,
                        etag: W/"CWNG:63e653e9-…:1:7be828578aae0f01"}]
                                              ECHOED BYTE-FOR-BYTE

Device integrity across the whole run: 30 `Bookmark` rows before and after, zero added, zero
removed, zero modified.

➡️ **Option B is viable and Option A is unnecessary.** CWNG does not need to reverse-engineer or
synthesize Kobo's composite manifest; byte equality against a CWNG-owned revision ETag is a sound
unchanged/changed test. The `W/"CWNG:<generation-id>:<authority-revision>:<digest-prefix>"` format
recommended in section 7.2 is the exact string that was accepted and echoed.

**[OBSERVED 2026-08-23 — unexplained, and it matters for rollback]** Serving `W/"0"` *after* the
device had adopted a CWNG token did **not** move the stored token back; it stayed on the CWNG
value. One attempt only, not a controlled test, and a follow-up cycle serving revision `2` could
not be driven because Nickel throttled further syncs. **Do not assume a served ETag can always
replace a stored one** — a rollback path that depends on overwriting the device's token is
unproven. Discriminating test: serve a *different* CWNG revision and see whether that is adopted;
if it is, `W/"0"` is being rejected specifically rather than adoption being conditional.

### 11.3 Empty-set ETag and deletion representation

**[OBSERVED 2026-08-23]** The empty-set token is `W/"0"`. Measured on the operator's Clara BW
(read-only DB copy `db-20260822-235633`): of the ten books carrying a non-blank
`content.AnnotationsSyncToken`, the four with zero `Bookmark` rows all hold exactly `W/"0"`, and
every book holding a composite manifest has at least one annotation. So an empty authoritative set
does have a representable, non-synthesised token, and the "do not synthesize a composite empty
token" restriction below is satisfied by replaying `W/"0"` rather than by refusing to serve.

**[OBSERVED 2026-08-23]** Two further properties of the composite manifest, from the same device,
comparing three snapshots taken 2026-08-21 00:20, 2026-08-22 10:43 and 2026-08-22 23:56:

* Entries are ordered by an **ASCII sort on the stable-ID hash**, not by creation time, position, or
  the leading letter. Observed on a 14-entry manifest whose leading letters run A,A,C,B,A,B,B,B,B,B,
  B,C,B,C while the hashes are strictly ascending.
* The **stable-ID hash is invariant across version bumps**; only the `<version-int>` moves. Three
  entries advanced their version between snapshots with byte-identical hashes.

**[OBSERVED 2026-08-23 — UNEXPLAINED, do not design against it yet]** The manifest is **not
one-book-one-annotation-set**. Six different books carry manifests with the *same fourteen*
stable-ID hashes but *different* per-entry version ints, and only one of those books (`1984`) has
fourteen annotations; two of them have none at all. Either the hash is not an annotation identity,
or the token is not scoped per book. This matters because section 7.3 requires that no ETag match
for one book may suppress or name another, and a manifest shared across books is exactly the shape
that would break that. **Resolve this before any ETag-equality comparison is trusted across books.**

**[ASSUMED]** The composite-manifest representation of deletion has still not been measured, and
neither has the derivation of the stable-ID hash (it is not a plain MD5/SHA1/raw-bytes base64 of
`BookmarkID` -- all four were tested against a 14-entry manifest and none matched).

**[ASSUMED — RECOMMENDATION]** Closing experiment: seed a book with zero annotations, capture GET body/header and next check; then create one annotation, sync, delete it, and capture every manifest transition. Until then, accept an empty seed only with exact device/upstream ETag equality and do not synthesize a composite empty token.

### 11.4 Server-initiated seed request and upstream pagination

**[ASSUMED]** A server-initiated upstream GET using the live authenticated Kobo request context has not been explicitly measured, nor has Kobo-cloud pagination for a naturally large annotation set.

**[ASSUMED — RECOMMENDATION]** Closing experiment: perform the one-time seed without waiting for Nickel's GET, verify auth and full payload equivalence, and seed the 584-highlight book through all upstream pages while checking stable ETags and exact total count.

### 11.5 `ExtraAnnotationData` / `attachments` carrier

**[OBSERVED]** `attachments` was `{}` in all 13 sampled annotations, and `chapterTitle` appeared in only 4 of 13.

**[OBSERVED]** In the Libra Colour database, non-NULL `ExtraAnnotationData` is an exact predicate for the 49 opaque markup rows, while notes and highlights have NULL. The property—not the type—is the hard gate.

**[ASSUMED]** Whether the wire `attachments` object carries `ExtraAnnotationData`, and whether a byte-exact nonempty attachment replay restores freehand content, remain unknown.

**[ASSUMED — RECOMMENDATION]** Closing experiment: proxy a Libra Colour book known to contain markup; correlate annotation IDs with the device DB's non-NULL `ExtraAnnotationData` rows; capture PATCH and complete cloud GET; determine whether and how `attachments` carries the opaque bytes; replay the complete set from a verified device backup; and prove every opaque row, `StartKey`, drawing, note, highlight, and unrelated book unchanged. Only this experiment may promote all-empty wire attachments from `unknown` to proof of `absent`, or permit unchanged replay of a `present` book. It can never remove the authoring prohibition for `present`.

## 12. Implementation acceptance criteria

1. **[ASSUMED — RECOMMENDATION]** No code path can return a CWNG-authored owned-book 200 annotation GET unless all feature and authority gates pass. The sole non-authoritative exception is a byte-transparent successful Kobo upstream response captured during the controlled seed/proxy path.
2. **[ASSUMED — RECOMMENDATION]** No PATCH-derived partial set can become authoritative.
3. **[ASSUMED — RECOMMENDATION]** Seed acceptance requires exact device/upstream ETag equality, exact per-ID palette-color reproduction, safe opaque-content evaluation, and a verified immutable seed artifact.
4. **[ASSUMED — RECOMMENDATION]** Every emitted native `location` byte range equals the stored raw location byte range.
5. **[ASSUMED — RECOMMENDATION]** Existing parsed columns and `cfi_range` continue to power the web reader; raw Kobo storage is additive.
6. **[ASSUMED — RECOMMENDATION]** Pagination over 584 annotations yields exactly the frozen snapshot set despite a concurrent live mutation.
7. **[ASSUMED — RECOMMENDATION]** An expired/bad token, DB error, malformed shape, backup failure, color mismatch, or unresolved conflict produces no success-shaped partial/empty claim.
8. **[ASSUMED — RECOMMENDATION]** Web and device conflicts resolve deterministically when clocks are usable and quarantine safely when they are not.
9. **[ASSUMED — RECOMMENDATION]** Both opt-ins default off, and the emergency override can only disable.
10. **[ASSUMED — RECOMMENDATION]** Migration is idempotent, preserves every existing annotation value, and leaves every legacy book unseeded rather than falsely authoritative.
11. **[ASSUMED — RECOMMENDATION]** Backup schema 3 and the seed artifact can restore the exact pre-replacement and seed-baseline sets, including a known-empty authoritative set.
12. **[ASSUMED — RECOMMENDATION]** Logs/metrics prove the trigger-to-effect chain without containing credentials or annotation text.
13. **[ASSUMED — RECOMMENDATION]** No create, edit, or delete can advance a Kobo book authority revision unless that exact book has `opaque_content_status='absent'`; a `present` or `unknown` book continues reading-position sync without Kobo annotation authoring.
14. **[ASSUMED — RECOMMENDATION]** An unfamiliar annotation type with NULL/proven-absent opaque content does not fail merely because the Clara never produced it; a non-NULL `ExtraAnnotationData` property blocks authoring regardless of type.

## 13. Deliberately excluded from the first implementation

**[ASSUMED — RECOMMENDATION]** Do not reverse-engineer or synthesize Kobo composite manifest entries in the first implementation.

**[ASSUMED — RECOMMENDATION]** Do not push CFI-only, unanchored, PDF, comic, or xpointer annotations to Kobo.

**[ASSUMED — RECOMMENDATION]** Do not author any book with `opaque_content_status` other than `absent`, and do not interpret, modify, omit, or synthesize opaque markup/attachment content.

**[ASSUMED — RECOMMENDATION]** Do not emit any `highlightColor` outside the five observed Kobo palette values and do not device-gate those values on greyscale versus color hardware.

**[ASSUMED — RECOMMENDATION]** Do not use field-level conflict merges, arrival-order tie-breaking, annotation counts as completeness proof, or a successful HTTP response as device acknowledgment.

**[ASSUMED — RECOMMENDATION]** Do not delete seed captures or exact materializations as part of ordinary rolling-three annotation backup retention. Their retention and user-facing restore tooling require a separate explicit policy.
