# Multi-device annotations: source-of-truth model and migration plan

Status: design only; no Kobo annotation-GET implementation is authorized

Date: 2026-08-09

Owners: backend model, API, and migrations in this document; **CWNG READER owns the SPA web reader; NEW5 owns SPA parity**

## 1. Decision summary and evidence boundary

CWNG should become the canonical, user-editable annotation store for many physical and logical
readers. The backend needs four concepts that are currently conflated or absent:

1. a first-class, user-visible `Device` with a mutable friendly name;
2. immutable annotation provenance (`origin_device_id`) distinct from mutable administrative
   assignment (`assigned_device_id`);
3. per-device/per-book delivery acknowledgments plus per-device/per-annotation materialization;
4. ordered annotation revisions, including the client modification time currently discarded.

**[OBSERVED]** `Annotation` is already intended as the canonical store for Kobo, web-reader, and
KOReader origins, but its only device-like field is `device_origin_id`, documented as the opaque
ID of a *row* a device wrote or saw, not the identity of the device (`cps/ub.py:908-958`). Its
uniqueness scope is user/book/annotation ID (`cps/ub.py:976-983`). **[OBSERVED]** KOReader progress
already accepts a device name and optional device ID (`cps/progress_syncing/protocols/kosync.py:1209-1216,1252-1282`), but that value is stored only on the winning progress row
(`cps/progress_syncing/models.py:449-476`), not in a registry.

**[OBSERVED — operator protocol capture/reference]** Kobo requests carry `x-kobo-deviceid`, model,
firmware, platform, affiliate, and OS headers. The referenced protocol note is not present on this
`origin/main` worktree, so this document does not invent line citations for it. The checked-in auth
research independently confirms that Kobo's `DeviceId` participates with `UserKey` in device auth
and is sent to `/v1/auth/device` (`cps/kobo_auth.py:22-36`). Today the reading-services gate resolves
the logged-in user but neither registers nor attaches a device (`cps/readingservices.py:117-139`).

Device identity materially improves synchronization, but it does **not**, alone, solve F-0af60c.
That distinction is a safety constraint, not a semantic quibble; §4 traces the residual race.

## 2. Device identity model

### 2.1 Tables

Add these app-database models in `cps/ub.py`:

```text
Device
  id                    INTEGER PK                         internal join key
  public_id             VARCHAR(36) NOT NULL UNIQUE       server-generated UUIDv4; API key
  user_id               INTEGER NOT NULL FK user.id       ON DELETE CASCADE
  kind                  VARCHAR NOT NULL                  kobo|koreader|webreader|future
  display_name          VARCHAR(120) NOT NULL             mutable user label
  model                 VARCHAR(120) NULL                 e.g. Kobo Libra Colour
  platform              VARCHAR(80) NULL                  normalized platform/engine
  firmware_version      VARCHAR(80) NULL                  last observed app/firmware
  first_seen_at         DATETIME NOT NULL
  last_seen_at          DATETIME NOT NULL
  last_metadata_at      DATETIME NULL                     guards stale metadata writes
  active                BOOLEAN NOT NULL DEFAULT 1        revoke/hide without losing history
  created_by            VARCHAR NOT NULL                  auto|web|migration

DeviceIdentity
  id                    INTEGER PK
  device_id             INTEGER NOT NULL FK device.id     ON DELETE CASCADE
  scheme                VARCHAR NOT NULL                  kobo-header|koreader-plugin|web-token
  key_version           INTEGER NOT NULL                  HMAC rotation version
  fingerprint           VARCHAR(64) NOT NULL              HMAC-SHA256 hex, never raw identifier
  first_seen_at         DATETIME NOT NULL
  last_seen_at          DATETIME NOT NULL
  UNIQUE(scheme, key_version, fingerprint)
```

`Device.public_id`, not the database integer or hardware identifier, is exposed to APIs and dropdown
clients. `display_name` is ordinary mutable user data; changing “Kobo Libra Colour” to “Maggie's
Libra” changes no foreign key and no sync identity. Auto-registration chooses a collision-resistant
label such as `Kobo Libra Colour`, then `Kobo Libra Colour (2)`. List responses return a derived
`annotation_count` rather than maintaining a drift-prone counter column.

`DeviceIdentity` is separate because identifiers rotate and one physical/logical device can acquire
a new protocol credential without losing attribution. It also allows an HMAC-key rotation window:
lookup against active key versions, attach the newest fingerprint to the same `Device`, and retire
old aliases after every active device has returned. A global uniqueness constraint is safe because
the fingerprint is instance-HMACed; an attempted cross-user match must be rejected rather than
silently reassigning a registered device.

Recommended indexes:

- `ix_device_user_active_last_seen(user_id, active, last_seen_at)` for dropdown/list ordering;
- `ix_device_user_display_name(user_id, display_name)` for search;
- identity uniqueness above for request lookup;
- no unique constraint on display name: two identical models are normal.

### 2.2 Privacy and identity derivation

A device identifier is stable personal-hardware metadata. Store only
`HMAC-SHA256(instance_device_identity_key, scheme || NUL || raw_identifier)` and a key version.
Do **not** store or log raw `x-kobo-deviceid`, KOReader installation UUIDs, bearer tokens,
`x-kobo-userkey`, cookies, IP history, affiliate name, or an unconstrained copy of every header.
Model, firmware, platform, first/last seen, and a user-chosen label are product-visible inventory
data and are appropriate to retain.

Plain hashing is insufficient for KOReader identifiers that may be short or user-chosen; an attacker
with a database copy could dictionary them. Kobo's current value appears SHA-256-length, but hashing
an already stable identifier still preserves linkability. HMAC makes a database-only disclosure
non-linkable outside this CWNG instance. The HMAC key belongs in the existing secret/config boundary,
not app.db. Rotation retains the prior key read-only long enough to match returning devices, then
adds a current-version alias. **[ASSUMED]** There is currently no general instance-secret rotation
facility suitable for this purpose; implementation must audit configuration before selecting its
storage mechanism.

Raw identifiers exist only for the duration of one authenticated request. Logs use `Device.public_id`
or an eight-character fingerprint prefix. API callers cannot submit or retrieve fingerprints.
Device deletion is soft (`active=false`) while any annotation provenance/state references it; a
hard delete is allowed only after explicit reassignment/anonymization and is not a routine endpoint.

### 2.3 Kobo registration

After `requires_reading_services_auth_and_config` has established `current_user`, a shared
`resolve_request_device(user)` helper reads the identity and descriptive headers, HMACs the raw
device ID, and upserts the registry. The current gate is the correct user-binding point because it
already distinguishes authenticated interception from unauthenticated proxying
(`cps/readingservices.py:117-139`). It must run for Kobo sync/library routes as well as reading
services if “last seen” is intended to mean any device activity.

Rules:

1. Missing/malformed identity does not create an “unknown Kobo” registry row shared by many
   devices. The request proceeds under existing behavior with `request_device=None` and a warning
   that contains no raw ID.
2. A new fingerprint creates one device for the authenticated user. A fingerprint already bound to
   another user is a security event and does not migrate ownership.
3. `last_seen_at` advances on every authenticated request. Model/firmware/platform update only from
   non-empty, length-bounded, control-character-free values and only when the observation is newer
   than `last_metadata_at`.
4. Affiliate and OS may inform normalized `platform`; their raw values are not retained unless a
   later adapter has a named product need.

This is passive registration. It does not change the current fact that GET, PATCH, and
`checkforchanges` proxy to Kobo (`cps/readingservices.py:346-397`).

### 2.4 KOReader registration

**[OBSERVED]** KOReader annotations reuse Basic/app-password authentication and checksum book
resolution (`cps/progress_syncing/protocols/koreader_annotations.py:7-23,287-328`), while the
progress PUT already accepts `device` and optional `device_id` (`cps/progress_syncing/protocols/kosync.py:1209-1216`). Authentication identifies the user, not the physical device
(`cps/progress_syncing/protocols/kosync.py:243-336`).

Extend the cwasync protocol compatibly:

- plugin installation creates and persists a random 128-bit installation UUID;
- all annotation GET/PUT calls send `X-CWNG-Device-ID`, `X-CWNG-Device-Name`,
  `X-CWNG-Device-Model`, and `X-CWNG-Client-Version` headers;
- progress PUT continues its body fields, and the server resolves the same registry from
  `device_id`/`device` when headers are absent;
- new plugin annotation PUTs may duplicate `device_id` in the top-level JSON for compatibility,
  but a conflicting header/body ID is rejected rather than guessed;
- old plugins without an ID remain functional but their writes have NULL device attribution.

The ID is an installation identity, not a hardware fingerprint. A KOReader reinstall will register
a new logical device unless the user re-links it through a future administrative action. This is
preferable to fingerprinting hardware. The current 100-character validation bounds are a useful
wire precedent (`cps/progress_syncing/protocols/kosync.py:78-82,1279-1282`).

### 2.5 Web reader registration

Represent each browser profile as `kind='webreader'`, not as a magic NULL device and not as one
global device for all users. Backend endpoint:

```http
POST /api/annotations/devices/web-session
Cookie: authenticated CWNG session
Body: {"existing_public_id": "optional UUID", "suggested_name": "optional bounded label"}
Response: {"device": {...}}
```

If `existing_public_id` belongs to the logged-in user and is an active web-reader device, touch it;
otherwise create a server public ID and a server-generated opaque web identity token. The browser
stores that opaque token/profile ID; it is not derived from UA, IP, canvas, or other fingerprinting.
Web mutation endpoints accept the public ID/token and bind it to `current_user`; absence falls back
to a per-user `CWNG Web Reader (legacy)` device only after the user actually mutates an annotation,
not merely on page view.

The API contract and model are backend scope. Storage/use in the actual SPA is owned by **CWNG
READER**, and any equivalent classic/new-SPA behavior is reconciled by **NEW5**; this document does
not prescribe their UI.

## 3. Annotation provenance, assignment, and heterogeneous metadata

### 3.1 Annotation columns

Add nullable FKs to `annotation`:

```text
origin_device_id        INTEGER FK device.id ON DELETE SET NULL   immutable after create
assigned_device_id      INTEGER FK device.id ON DELETE SET NULL   mutable administrative target
last_editor_device_id   INTEGER FK device.id ON DELETE SET NULL   latest accepted mutation actor
client_created_at       DATETIME NULL                              source creation time
client_modified_at      DATETIME NULL                              accepted source mutation time
client_clock_kind       VARCHAR NULL                               device|server|unknown
server_modified_at      DATETIME NOT NULL                          accepted-at server time
revision                INTEGER NOT NULL DEFAULT 1                 monotonic canonical revision
routing_revision        INTEGER NOT NULL DEFAULT 1                 assignment/delivery-plan revision
```

Keep `source` for protocol/engine origin; it is not a substitute for device attribution. Keep
`device_origin_id` under that exact existing name as the remote/native row ID, but clarify it or
eventually alias it as `origin_native_annotation_id`. Renaming immediately is needless migration
risk. Its current documented semantics are feedback-loop suppression (`cps/ub.py:954-958`), and the
portable bridge reads/writes it on the wire (`cps/services/annotation_portable.py:58-90,159-186`).

`origin_device_id` and `client_created_at` join the immutable creation anchor: `annotation_id`,
book, origin/source, highlighted passage, and all locator fields. Current web editing already limits
mutation to note/color and calls position immutable (`cps/annotations.py:709-728`). Changing an
anchor creates a new annotation and tombstones the old one; it is not an edit. This prevents a
late device update from moving an existing identity to a different passage.

### 3.2 Origin is not assignment

The addendum is correct: hard device ownership conflicts with both web editing and dropdown
reassignment. Model two separate facts:

- `origin_device_id`: immutable historical attribution—where the annotation was created;
- `assigned_device_id`: mutable administrative attribution/routing—where the user currently wants
  it categorized or delivered.

Creation normally sets both to the request device. Historical rows keep both NULL. Reassignment
never rewrites origin. A web edit sets `last_editor_device_id` to the browser device and advances
the canonical revision, but it does not steal origin or silently change assignment.

One assigned device is not enough to represent copies on many readers. Add:

```text
AnnotationDeviceState
  id                    INTEGER PK
  annotation_id         INTEGER NOT NULL FK annotation.id ON DELETE CASCADE
  device_id             INTEGER NOT NULL FK device.id ON DELETE CASCADE
  native_annotation_id  VARCHAR NULL          this device's local row ID
  desired               BOOLEAN NOT NULL       routing intent, never deletion-by-omission
  delivery_status       VARCHAR NOT NULL       pending|delivered|acknowledged|incompatible|blocked
  first_seen_revision   INTEGER NULL
  last_delivered_revision INTEGER NULL
  last_ack_revision     INTEGER NULL
  last_seen_present_at  DATETIME NULL
  content_fingerprint   VARCHAR(64) NULL        rendered artifact/anchor generation
  native_metadata_json  TEXT NULL               bounded adapter envelope, §3.4
  last_error_code       VARCHAR NULL
  updated_at            DATETIME NOT NULL
  UNIQUE(annotation_id, device_id)
```

Thus assignment is the primary dropdown choice and delivery intent, while the join records actual
copies on origin, former, and additional devices. Reassigning A→B sets `assigned_device_id=B`,
increments `routing_revision`, marks/creates B's state `desired=true,pending`, and sets A's
`desired=false` **without deleting anything on A**. A device-local deletion requires an explicit,
hardware-proven delete operation and acknowledgment. Reassignment is not deletion.

### 3.3 Meaning of “locked by ereader id”

There are three plausible interpretations:

| Interpretation | Benefit | Cost/conflict |
|---|---|---|
| Attribution lock | Immutable origin and audit actor identify every contribution. | Does not prevent another device/user-authorized web editor from changing mutable fields. |
| Ownership/authority lock | Only origin can mutate all or selected fields. | Directly contradicts “web reader can edit any annotation” and dropdown reassignment; strands data after device loss. |
| Conflict-scope lock | Per-device clocks/acks distinguish retries, delivery, and concurrent edits. | Requires registry/state/revisions and still cannot see unuploaded local data. |

Recommend **immutable attribution plus device-scoped conflict/delivery state, not hard ownership**.
The lock is: origin and creation anchor cannot be rewritten; every accepted mutation records its
actor; devices can mutate only the annotation fields the adapter is authorized to express; the
authenticated CWNG user/admin may edit mutable fields and reassign. This preserves accountability
without making a lost e-reader the permanent owner of a note.

Hard ownership would require a privileged “take ownership” bypass for the web reader, at which
point it is neither simpler nor safer: it adds lockout states but still permits override. Soft
administrative assignment accurately matches the operator's dropdown requirement.

### 3.4 Heterogeneous native metadata

**[OBSERVED]** Canonical typed columns already cover Kobo container paths/child indexes/offsets,
context, chapter progress, and CFI (`cps/ub.py:931-945`); PDF quads and comic pages
(`cps/ub.py:942-948`); and deliberately separate KOReader xpointers that are unsafe to present to
epub.js as CFIs (`cps/ub.py:949-953`). The portable adapter preserves both KoboSpan and xpointer
representations (`cps/services/annotation_portable.py:58-80,137-168`).

Do not add one unconstrained `extras` blob to `Annotation`. Native metadata belongs to a particular
device's materialization, so put a **bounded, namespaced JSON envelope** on
`AnnotationDeviceState.native_metadata_json`:

```json
{
  "schema": 1,
  "adapter": "kobo",
  "fields": {"chapterTitle": "…", "firmwareColorCode": 0}
}
```

Each adapter owns an allowlist and JSON schema. Enforce maximum 16 KiB encoded size, maximum depth
4, maximum 64 keys, bounded strings, JSON scalar/list/object types only, and reject secret/identity
keys. Canonicalize before storage. Unknown fields are rejected, not blindly retained. Native
attachments/ink strokes belong in a separately size-controlled attachment table/blob design, not
base64 in this JSON.

Promotion rule: a field needed by two adapters, queried/sorted/exported, used for conflict
resolution, or required to render becomes a typed canonical column via migration. The JSON is for
lossless round-trip metadata with bounded known semantics—not a data lake. This hybrid preserves
heterogeneity without making product queries depend on SQLite JSON extraction.

## 4. Per-device state, generations, and F-0af60c

### 4.1 Book-level state

Add:

```text
DeviceBookAnnotationState
  id                      INTEGER PK
  device_id               INTEGER NOT NULL FK device.id ON DELETE CASCADE
  book_id                 INTEGER NOT NULL
  content_fingerprint     VARCHAR(64) NULL
  last_offered_generation INTEGER NULL
  acknowledged_generation INTEGER NULL
  last_delivered_etag     VARCHAR(255) NULL
  acknowledged_etag       VARCHAR(255) NULL
  last_seen_set_digest    VARCHAR(64) NULL
  last_pull_at            DATETIME NULL
  last_push_at            DATETIME NULL
  safety_state            VARCHAR NOT NULL DEFAULT 'observe_only'
  UNIQUE(device_id, book_id)
```

Book annotation generations are server monotonic integers. A separate
`UserBookAnnotationGeneration(user_id, book_id, generation, set_digest, updated_at)` row avoids
deriving state from `max(last_synced),count`, which cannot uniquely identify a set. Every accepted
create/edit/delete increments it in the same transaction as the annotation revision.

`last_offered_generation` means bytes were successfully served, not acknowledged. A later request
that presents the exact delivered etag can advance `acknowledged_generation`; this is **[INFERRED]**
from the observed etag flow, not yet proven for Nickel. `last_seen_set_digest` is derived from
`AnnotationDeviceState` rows, never a giant JSON ID array. The join identifies what CWNG has evidence
the device saw and keeps queries indexed.

### 4.2 What identity solves

Without device identity, one device echoing an etag can make the server believe every device is
current. With identity:

- device B's acknowledgment never advances device A's watermark;
- retry/delivery decisions are per device and book;
- a reassign to unseen B creates B `pending` state without altering A;
- explicit device deletions can be scoped to IDs that device previously acknowledged;
- different content fingerprints prevent replaying stale KoboSpan/xpointer metadata across a
  regenerated artifact;
- diagnostics can say “Maggie's Libra is at generation 12; Clara is at 9.”

This is a real correction to the old model. KOReader's existing deletion code already explains why
omission cannot distinguish “deleted here” from “never present here” and requires named deletes
(`cps/progress_syncing/protocols/koreader_annotations.py:25-38,93-150`). Device-specific last-seen
state makes those named-delete checks stronger.

### 4.3 What identity does not solve—the residual F-0af60c race

Concrete trace:

1. A acknowledged server generation 10.
2. A creates annotation `a-local` offline; CWNG cannot record it because no PATCH/push occurred.
3. B edits a note; server becomes generation 11.
4. A's next flow issues GET before uploading `a-local`.
5. Identity tells CWNG this is A at generation 10. It does **not** reveal `a-local`.
6. If Nickel treats generation 11 as a replacement set and deletes IDs omitted by the server, A
   can erase `a-local` before PATCH.

No per-device server watermark can contain a row the server has never observed. The
`checkforchanges` request names only content/etag in the archived reverse-engineering, and the
device header adds *who*, not a dirty-ID list. Therefore the operator's framing is partly correct:
identity makes per-device generations representable without a protocol body change, but it is not
sufficient to resolve the unuploaded-write race.

For modifiable clients (cwasync/web reader), solve it by protocol ordering: push local mutations and
receive a server acknowledgment before pulling a newer generation; named deletions only; never
delete by omission. The existing KOReader bridge already follows named deletion semantics
(`cps/progress_syncing/protocols/koreader_annotations.py:330-384`). For stock Nickel, CWNG cannot
force upload-before-download. A “delay one cycle” or recent-PATCH quiescence heuristic only narrows
the window; a user can create a highlight immediately after the last PATCH.

Accordingly, stock-Kobo delivery stays `observe_only` until F-3b565b hardware work proves at least
one of:

- Nickel merges server results and preserves unknown local IDs;
- Nickel uploads dirty annotations before destructive reconciliation under the actual flow; or
- a response/delete contract exists that is explicitly additive/named rather than replacement by
  omission.

If none holds, server-authored native GET is unsafe regardless of the registry. The safe product
path would remain capture/import or a controllable client/plugin.

## 5. Ordering, F-0a69bc, and revision history

### 5.1 Prerequisite columns and ingest

**[OBSERVED]** Inbound Kobo annotations declare `clientLastModifiedUtc`
(`cps/readingservices.py:333-341`), but `_upsert_annotation` reads ID, content, and location and then
sets only server `_now()` into `last_synced` (`cps/services/annotation_sync/__init__.py:133-193`).
Cross-device last-write-wins cannot be reconstructed from server arrival time.

**Safe-slice implementation note (2026-08-09):** the additive ingest-only slice persists the
nullable `client_modified_at` value but intentionally does not pretend that the future actor-based
tie-break already exists. A malformed value is rejected from local storage without changing the
proxied Kobo response; an older, undated-over-dated, or equal-clock update is a local no-op. Equal
clock must remain a no-op until an annotation actor/device key and revision journal are populated,
because applying the §5.2 tie-break without its actor input would be nondeterministic. This is a
staging constraint, not the final multi-device conflict implementation
(`cps/services/annotation_sync/__init__.py:154-207`).

Add `client_created_at`, `client_modified_at`, `client_clock_kind`, `server_modified_at`, and
`revision` as specified in §3.1. Kobo ingest parses `clientLastModifiedUtc` as strict timezone-aware
UTC, rejects impossible/out-of-range values from ordering (while retaining the mutation for review),
and stores the raw semantic value only as normalized UTC. Do not overwrite `created_at` on update;
for a new row, initialize `client_created_at` from a distinct source field if one is ever observed,
otherwise from `clientLastModifiedUtc` with `client_clock_kind='device-inferred-create'`.

`last_synced` remains compatibility/“last processed” time during transition. New conflict code uses
the explicit columns; overloading `last_synced` again would preserve F-0a69bc under a new name.

### 5.2 Conflict rule

Maintain an append-only `AnnotationRevision` audit table:

```text
id, annotation_id(FK), revision, mutation_kind,
actor_device_id(NULL allowed), source, client_modified_at,
server_received_at, payload_digest, accepted, rejection_reason,
changed_fields_json, prior_revision, UNIQUE(annotation_id, revision)
```

Do not duplicate highlighted/note text into every revision initially; `changed_fields_json` stores
field names and old/new hashes plus non-sensitive routing facts. Full user-visible undo/history is a
later product decision because duplicating passages changes backup/privacy/storage behavior.

For mutable fields (note, color, hidden), compare candidates as follows:

1. Same device + same client timestamp + same payload digest is an idempotent retry.
2. Valid client clocks: greater `client_modified_at` wins.
3. Equal timestamps with differing payloads: deterministically compare
   `(actor_device.public_id, payload_digest)`; the winner is stable on retry. Record the loser.
4. Web-reader edits use server time as their trusted client time because CWNG receives the user
   action directly. They may edit any origin but do not move the immutable anchor or provenance.
5. Missing/invalid client clock on an existing row does not overwrite a different device's newer
   value. It may create a new annotation, fill a NULL mutable field, or update a row whose last
   accepted editor is the same device; otherwise return/record a conflict requiring web resolution.
6. Every accepted semantic mutation increments annotation `revision` and the user/book generation
   atomically. Reassignment increments `routing_revision` and book delivery generation but not the
   content revision.

KOReader should add `client_modified_at` and a per-installation monotonic `mutation_id` to the
portable payload. Older clients have no trustworthy client clock: use rule 5, with server receipt
time marked `client_clock_kind='server-fallback'`, never pretending it is device time. Web reader
has a trustworthy server-side action order. Kobo device clocks may be wrong; clamp or quarantine
timestamps far outside an implementation-defined window and surface the conflict. **[ASSUMED]**
Nickel's timestamp quality across manual clock changes is not yet measured.

This is deterministic LWW, not causality. Two offline edits to different fields can still lose one
field because Kobo sends a whole annotation rather than field-level vector clocks. Field-specific
merge could preserve independent note/color edits later, but must not be inferred without knowing
which fields the device intentionally changed.

## 6. Backend API and export/query contracts

### 6.1 Device API

All endpoints require the existing logged-in user/session (or an equivalent scoped API auth), and
all device IDs are resolved under `Device.user_id == current_user.id`:

```text
GET   /api/annotations/devices
      ?active=true&include_counts=true
PATCH /api/annotations/devices/<public_id>
      {label}
GET   /api/annotations/devices/<public_id>/delete-preflight
DELETE /api/annotations/devices/<public_id>
POST  /api/annotations/devices/<public_id>/restore
POST  /api/annotations/devices/web-session
GET   /api/annotations/devices/<public_id>/annotations
      ?book_id=&include_hidden=false&limit=&cursor=
```

List shape includes public ID, display name, kind, model, firmware, first/last seen, active, current
assigned annotation count, and optionally origin count. Do not expose identity fingerprints. Device
rename uses optimistic concurrency; deactivation does not null provenance.
Deletion is soft: it retains the device and label, leaves every immutable `origin_device_id`
unchanged, and clears current assignments after snapshotting them for restore. Preflight returns
`origin_count` and `assigned_count`. Restore reactivates the same public ID and restores snapshotted
assignments that remain unassigned; it reports conflicts rather than overwriting a later assignment.

### 6.2 Single and bulk reassignment

```http
PATCH /annotations/<book_id>/<annotation_id>
{"assigned_device_id":"... or null","expected_routing_revision":4}

POST /api/annotations/assignments/bulk
{
  "items":[{"book_id":539,"annotation_id":"...","expected_routing_revision":4}],
  "assigned_device_id":"... or null"
}

Response: `{"results":[{"annotation_id":"...","ok":true},{"annotation_id":"...",
"ok":false,"error_code":"revision_conflict"}]}` with HTTP 200.
```

Rules:

- source annotation and target device must belong to the authenticated user; foreign IDs return
  404, following the current annotation owner-scoped lookup (`cps/annotations.py:694-706`);
- target must be active; compatibility can yield 409 `device_incompatible` rather than accepting a
  delivery that can never render;
- single reassignment is atomic; bulk is capped at 500 and commits each item independently. Mixed
  successes/failures return HTTP 200 with per-item error codes, so one stale revision does not roll
  back successful siblings and clients can safely chunk the real 559-row case;
- NULL means “unassigned,” not delete and not “all devices”;
- origin never changes;
- create/update target `AnnotationDeviceState(desired=true,pending)` and retain old state with
  `desired=false`; never infer a local deletion;
- advance routing/book generation so B can be offered the row only after its adapter is safe;
- write an accepted assignment audit revision with the web editor device as actor.

Reassigning to a device that has never seen the annotation does **not** immediately claim delivery.
Its state is pending with NULL ack fields. For KOReader/cwasync, next push-first sync can deliver and
ack it. For Kobo, pending remains blocked/observe-only until F-3b565b permits native delivery.

### 6.3 Query and export shape—no N+1

Current exports use one annotation query and project rows into stable MD/CSV/JSON fields
(`cps/annotations.py:250-308,338-362`). Extend the base query with two aliased outer joins:

```sql
SELECT annotation, origin_device, assigned_device
FROM annotation
LEFT JOIN device AS origin_device ON origin_device.id = annotation.origin_device_id
LEFT JOIN device AS assigned_device ON assigned_device.id = annotation.assigned_device_id
WHERE annotation.user_id = :user_id [AND annotation.book_id = :book_id]
ORDER BY ...
```

Use `contains_eager`/aliased projections or explicit columns; do not access lazy relationships per
row. Add export columns:

```text
origin_device_id, origin_device_name, origin_device_type, origin_device_model,
assigned_device_id, assigned_device_name, assigned_device_type,
last_editor_device_id, client_modified_at, revision
```

JSON schema increments from the current `schema_version: 1` (`cps/annotations.py:350-362`). Markdown
adds human-readable origin/assignment metadata but no stable hardware fingerprint. CSV keeps stable
column order. A per-device export reuses the same joined query filtered on
`assigned_device_id`, with an explicit `role=origin|assigned|present` option rather than ambiguous
“device.”

Device list counts use one grouped query, not one query per device:

```sql
SELECT device.id, COUNT(annotation.id)
FROM device
LEFT JOIN annotation
  ON annotation.assigned_device_id=device.id AND annotation.hidden IS NOT TRUE
WHERE device.user_id=:user_id
GROUP BY device.id
```

Index `annotation(user_id, assigned_device_id, hidden)` and
`annotation(user_id, origin_device_id)` to keep dropdown counts and per-device sheets cheap at 595
rows and beyond. Actual dropdown/table interaction belongs to **CWNG READER**; parity is **NEW5**.

## 7. Migration plan

### 7.1 Forward migration shape

Implement one versioned `migrate_multi_device_annotations(engine, session)` registered after the
existing annotation migrations. Existing code registers decouple, polymorphic position,
`device_origin_id`, and KOReader identity in that order (`cps/ub.py:2851-2855`). Follow the live
`PRAGMA table_info` guards and duplicate-column handling used by
`migrate_annotation_device_origin` (`cps/ub.py:2615-2648`), because inspector state can be stale
after table renames (`cps/ub.py:2549-2567`).

Within `engine.begin()`:

1. `CREATE TABLE IF NOT EXISTS device`, `device_identity`,
   `user_book_annotation_generation`, `device_book_annotation_state`,
   `annotation_device_state`, and `annotation_revision`, with named FKs/uniques.
2. Create indexes with `IF NOT EXISTS`.
3. If `annotation` is absent, return; fresh `create_all` already owns its full model shape.
4. Read actual annotation columns with `PRAGMA table_info(annotation)`.
5. Add each missing nullable FK/time/string column individually. Add
   `revision INTEGER NOT NULL DEFAULT 1`, `routing_revision INTEGER NOT NULL DEFAULT 1`, and
   `server_modified_at DATETIME NULL` initially, because SQLite cannot safely backfill a dynamic
   UTC default through `ALTER TABLE`.
6. Backfill only `server_modified_at = COALESCE(last_synced, created_at)` and leave all device/client
   fields NULL. Then verify zero NULL server times before a later table rebuild or ORM-level
   non-null invariant is enforced.
7. Seed `user_book_annotation_generation` with one row per existing `(user_id,book_id)`, generation
   1 and a deterministic digest of the canonical current rows. This records a baseline, not a claim
   that any device acknowledged it.
8. Do **not** create `Device` rows or `AnnotationDeviceState` rows for historical annotations.
9. Sanity checks: annotation count before/after must match exactly; every annotation ID/book/user,
   text, note, locator, hidden flag, source, and `device_origin_id` remains byte/value-identical;
   `PRAGMA foreign_key_check` returns clean; generation group count matches distinct user/book
   groups. Any mismatch raises and rolls back.

Per-statement duplicate-column catches make interrupted/concurrent re-entry idempotent, as in the
existing migration (`cps/ub.py:2636-2648`). Table/index creation is idempotent. Backfills are
`WHERE server_modified_at IS NULL`; generation inserts use conflict-do-nothing and must not bump an
existing live generation on reboot.

### 7.2 The 595 historical annotations

All 595 survive with:

- `origin_device_id=NULL`—unknown, not “Kobo device” and not a fabricated shared legacy device;
- `assigned_device_id=NULL`—shown/exported as “Unattributed” until the user chooses;
- `last_editor_device_id=NULL`;
- `client_created_at/client_modified_at=NULL`, because server timestamps cannot be relabeled as
  client clocks;
- `revision=1`, `routing_revision=1`;
- existing `source`, `device_origin_id`, content, anchors, and lifecycle untouched.

This preserves uncertainty. `source='kobo'` says which ingestion protocol produced the row, not
which physical Kobo did so. A later device cannot retroactively claim historical origin merely
because it presents the same annotation ID; it may become `last_editor_device_id` and acquire an
`AnnotationDeviceState`, while origin stays unknown unless the user explicitly assigns (assignment,
not provenance).

Do not backfill 595 full `AnnotationRevision` snapshots: that duplicates user passages and falsely
implies observed revision history. History begins with the first post-migration mutation; the base
row at revision 1 is the audit baseline.

### 7.3 Rollback/downgrade

Operational rollback is first **code rollback without schema rollback**: old code ignores additive
tables/columns and preserves every annotation. This is the safest reversible path.

Provide a separately invoked downgrade routine for test/release rollback only:

1. export device/provenance/assignment/state/revision tables to a timestamped JSON artifact;
2. verify annotation count and canonical content digest;
3. drop new indexes/tables in reverse dependency order;
4. drop additive annotation columns only where SQLite supports it, otherwise rebuild `annotation`
   transactionally from the pre-migration column list;
5. verify the same annotation count/content digest before commit.

Downgrade cannot preserve new provenance/conflict history in the old schema; the export makes that
loss explicit and recoverable on re-upgrade. It must never delete or rewrite the canonical 595
annotation rows. Re-running the forward migration after a code-only rollback is a no-op; after a
true schema downgrade, it recreates empty device state while preserving annotation content.

### 7.4 Migration tests

- fresh database ORM/create-all shape;
- legacy annotation table with exactly 595 varied rows, NULLs, hidden rows, all position types, and
  non-ASCII text: byte/value equality before/after;
- second and third migration invocation produce identical schema/data/generations;
- injected failure mid-DDL/backfill rolls back without row loss;
- historical rows remain unattributed;
- DB with organic device/generation state is not reset on reboot;
- code rollback reads/edits annotations while extra schema remains;
- downgrade export + rebuild round-trip preserves canonical digest;
- FK cascade tests for user deletion and annotation deletion, and SET NULL behavior for any
  administrative hard-device deletion.

## 8. Sequencing and safety gates

### Phase A—safe, protocol-passive foundation

1. Add tables/columns, idempotent migration, invariants, backup/export schema changes.
2. Fix F-0a69bc: store normalized Kobo client modification times and explicit server receipt time.
3. Passively register Kobo devices from authenticated headers; do not change proxy responses.
4. Register KOReader devices from existing progress identity and new optional cwasync headers.
5. Add backend device list/rename/deactivate APIs, joined annotation queries, counts, exports, and
   immutable origin/mutable assignment APIs.

These can be tested without authoring anything to a Kobo. Header registry tests must prove raw IDs
and credentials never enter DB/log/API.

### Phase B—controllable two-way clients

1. Extend cwasync with installation identity, client timestamps/mutation IDs, push-before-pull,
   named deletions, and explicit acknowledgments.
2. Add per-device book/annotation state and conflict audit under KOReader integration tests.
3. Expose backend web-reader device/mutation contracts. **CWNG READER owns their SPA consumption;
   NEW5 owns parity.**
4. Verify reassignment A→B delivers to compatible controllable B, never deletes from A by omission,
   and reports pending/acknowledged state.

### Phase C—Kobo observation and hardware gate

1. Populate Kobo `DeviceBookAnnotationState` only from observed requests/etags, initially
   `safety_state='observe_only'`.
2. Use the separate transparent capture experiment to determine successful body, etag behavior,
   merge/replacement semantics, 304/error behavior, content-generation binding, and F-3b565b.
3. Specifically test a local unuploaded annotation while a second device advances server state.

**Nothing in this design authorizes CWNG to answer
`GET /api/v3/content/<id>/annotations`.** Current GET and `checkforchanges` remain proxies
(`cps/readingservices.py:346-397`). Native Kobo delivery is a separate decision after the hardware
safety gate. If Nickel deletes unknown local rows by omission and offers no additive contract, that
phase does not ship; the registry and controllable-client product remain valuable independently.

## 9. Acceptance criteria for a later implementation

1. Every newly observed capable device has one user-visible registry row and no raw hardware ID at
   rest.
2. Friendly rename does not change public ID, identity lookup, provenance, assignment, or ack state.
3. Every new annotation records immutable origin when known; legacy rows remain honestly NULL.
4. Web-authenticated edits can change mutable fields on any origin, record the web actor, and cannot
   change anchor/origin.
5. Single/bulk reassignment is user-scoped, optimistic, atomic, and never deletes by omission.
6. Device B's ack cannot advance A's generation; unacknowledged delivery is never called synced.
7. Invalid/missing client clocks cannot silently overwrite another device's newer accepted edit.
8. Native metadata is adapter-validated, bounded, and attached to device materialization.
9. Device lists, annotation sheets, and exports use joined/grouped queries with no N+1.
10. Migration preserves the exact 595-row canonical annotation corpus and is idempotent.
11. All Kobo behavior remains observational until F-3b565b is resolved on hardware.
