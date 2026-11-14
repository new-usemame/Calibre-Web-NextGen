# Progress Syncing Module

Syncs reading progress across e-readers using file checksums for book identification.

## Quick Start

```python
from cps.progress_syncing import calculate_and_store_checksum, get_latest_checksum

# Generate and store checksum
checksum = calculate_and_store_checksum(
    book_id=123,
    book_format='EPUB',
    file_path='/calibre-library/Author/Book/book.epub'
)
# Returns: '9e107d9d372bb6826bd81d3542a419d6'

# Retrieve for sync lookup
checksum = get_latest_checksum(book_id=123, book_format='EPUB')
```

## Structure

```
cps/progress_syncing/
├── models.py              # Database schema
├── checksums/
│   ├── koreader.py       # Partial MD5 algorithm
│   └── manager.py        # Storage and retrieval
└── protocols/
    └── kosync.py         # KOSync protocol (KOReader)
```

## Checksum Algorithm

KOReader partialMD5: Samples 1024 bytes at 11 positions (0, 4K, 16K, 64K, 256K, 1M, 4M, 16M, 64M, 256M, 1G) instead of hashing entire file. Returns 32-character MD5 hex.

**Auto-generated on:**
- Book import
- Metadata enforcement
- EPUB fixes
- Format conversion
- OPDS download with embedded metadata
- Container startup (backfills existing books)

**History tracking:** All checksums stored indefinitely. New checksums added alongside old ones when files change, enabling sync with any file version on user devices.

## Database Schema

```sql
CREATE TABLE book_format_checksums (
    id INTEGER PRIMARY KEY,
    book INTEGER REFERENCES books(id),
    format TEXT,           -- 'EPUB', 'PDF', etc.
    checksum TEXT(32),     -- MD5 hex
    version TEXT,          -- 'koreader'
    created TIMESTAMP
);
CREATE INDEX idx_checksum_lookup ON book_format_checksums(checksum, format);
```

Stored in `metadata.db`. Query orders by `created DESC` to prefer latest checksums.

## API

### Checksum Management

```python
from cps.progress_syncing import (
    calculate_koreader_partial_md5,
    calculate_and_store_checksum,
    get_latest_checksum,
    get_checksum_history
)

# Calculate without storing
checksum = calculate_koreader_partial_md5('/path/to/book.epub')
# => '9e107d9d372bb6826bd81d3542a419d6' or None

# Calculate and store
checksum = calculate_and_store_checksum(
    book_id=123,
    book_format='EPUB',
    file_path='/path/to/book.epub'
)

# Get latest
checksum = get_latest_checksum(book_id=123, book_format='EPUB')

# Get history (newest first)
history = get_checksum_history(book_id=123, book_format='EPUB')
# => [('abc...', 'koreader', '2025-11-09T10:30:00'), ...]
```

### Book Lookup

```python
from cps.progress_syncing.protocols.kosync import get_book_by_checksum

result = get_book_by_checksum('9e107d9d372bb6826bd81d3542a419d6')
if result:
    book_id, format, title, path, version = result
```

## KOSync Protocol

HTTP Basic Auth endpoints for KOReader devices:

- `GET /kosync` - Plugin download
- `GET /kosync/users/auth` - Authentication
- `GET /kosync/syncs/progress/<checksum>` - Get progress
- `PUT /kosync/syncs/progress` - Update progress

Progress stored in `kosync_progress` table (app.db). Updates `ReadBook` and `KoboReadingState` tables for web UI and Kobo device sync.

The full list of routes is in [Routes](#routes) below.

### Position encodings and `position_kinds`

Two encodings travel on `GET /kosync/syncs/progress/<document>`, and a client
has to say which it can act on. This section is the reference for third-party
clients; the stock KOReader plugin and our `cwngsync.koplugin` already comply.

**`locator`** — the original encoding. `progress` is an engine-private crengine
xpointer, or a page number for a paged document. The reader seeks to it exactly.

**`percentage`** — a position written by a producer that knows how far through
the book the reader is but cannot express it as a locator this engine would
understand: the web reader, whose positions are EPUB CFIs, and a Kobo, whose
positions are its own location tuples. `progress` is `null` and `percentage`
carries the position. Approximate by nature.

Advertise support with a comma-separated `position_kinds` query parameter:

```
GET /kosync/syncs/progress/<document>?position_kinds=locator,percentage
```

| Value | Meaning |
|---|---|
| `locator` | Client can seek to an xpointer or page number. Implied always. |
| `percentage` | Client can seek to a percentage of the book. |

**The default — an absent parameter — is `locator` only, and percentage-only
rows are withheld entirely.** That is deliberate, not an oversight. A plugin
released before this encoding existed guards only on `progress == nil`, and in
Lua `"" ~= nil`, so any non-null placeholder reaches `GotoXPointer` and is
stored as the document's `last_xpointer` — an unresolvable position the device
cannot recover from. Serving `null` instead clears that hazard but makes the
same plugin report a sync error where it used to say "no progress found". So
those clients are served nothing at all and behave exactly as they did before.

Responses carry a `position_kind` field naming the encoding of what was sent:

```jsonc
// locator
{ "document": "abc…", "progress": "/body/DocFragment[12]/body/div/p[3].0",
  "position_kind": "locator", "percentage": 0.4567, "device": "KOReader", … }

// percentage-only, to a client that advertised `percentage`
{ "document": "abc…", "progress": null,
  "position_kind": "percentage", "percentage": 0.4567, "device": "Web reader", … }
```

Check the `progress` value as well as the `position_kind` label. The label is
added by the server and the value is the stored column; if a proxy strips the
label, a client that trusts it alone would treat the percentage-only marker as
a locator. Our plugin's `SyncLogic.resolveRemotePosition` recognises the value
itself for that reason.

When a position exists but is being withheld because the client did not
advertise its encoding, the otherwise-empty response names what it is holding:

```jsonc
{ "position_kinds_available": ["percentage"] }
```

This appears only on that miss, never beside a served position, so its presence
means "ask for these and you get something". A book with no progress at all
returns `{}`. The server also logs the withholding at WARNING with the
parameter to send, so it is diagnosable from the server side too.

With Share my place on (the default), a device that is not sharing keeps its
place to itself. When the only places for a book belong to such devices, the
otherwise-empty response says how many of the account's other active devices
hold one (#2841):

```jsonc
{ "places_not_shared": 1 }
```

It is a count only, for the same account, and never names a device or a
position. A client can use it to explain why no place arrived: places travel
between devices only when both the device that set the place and the one asking
have Share my place on. `GET /kosync/users/auth` sent with `X-CWNG-Device-ID` for a device
the server already knows also returns `"share_place": true|false` for that
device; an unknown device gets no such field.

`percentage` is a decimal fraction on the wire (`0.4567` = 45.67%), which is
KOReader's format; it is stored as 0–100 internally. For a client that does
not send `X-CWNG-Device-ID` (below), progress is furthest-wins across
producers, so a lower percentage is dropped rather than dragging a
device backwards — a deliberate flip back to re-read a chapter does not travel.

The `PUT /kosync/syncs/progress` reply says what happened to the push (#2843),
on top of KOReader's `document` and `timestamp`:

```jsonc
{ "document": "…", "timestamp": 1794175156,
  "accepted": false, "shared": true, "kept_percentage": 0.3 }
```

`accepted` is true when this push is now the place kept for it: the shared
place, or the device's own place when that device is not sharing. `shared` is
false when the device's Share my place is off, so the place stays on that
device. When `accepted` is false, `kept_percentage` is the place kept instead,
as a fraction like `percentage`. Stock KOReader ignores all three.

### Feature detection: `GET /kosync/users/auth`

A client should not guess what this server supports from its version. The
auth reply names it:

```bash
curl -s -u alice:APP_PASSWORD \
     -H 'X-CWNG-Device-ID: 3f2a9c1e-reader-1' -H 'X-CWNG-Device-Name: Reader 1' \
     https://cwng.example/kosync/users/auth
```

```jsonc
{ "authorized": "OK",
  "capabilities": ["position_kinds", "percentage_push", "anchor", "book_id_document",
                   "annotations_text_quote", "sleep_screen_v1"],
  "share_place_gates": true,
  "share_place": false }   // only for a device the server already knows
```

| Capability | What it means |
|---|---|
| `position_kinds` | `GET` honours `?position_kinds=` (see above). |
| `percentage_push` | `PUT /kosync/syncs/progress` accepts `position_kind: "percentage"` with no `progress`. |
| `anchor` | A percentage `PUT` may carry an `anchor`, and `GET` returns one on `?position_kinds=…,anchor`. |
| `book_id_document` | A decimal Calibre book id is accepted as `document`, alongside the KOReader digest. |
| `annotations_text_quote` | `/kosync/syncs/annotations` places pushed `text_quote` highlights and, on `?text_quote=1`, names pulled ones by their words. |
| `sleep_screen_v1` | The sleep-screen routes used by our own plugin. |

`share_place_gates` is `true` when Share my place is enforced for this account
(the default; an administrator can turn it off with `CWNG_SHARE_PLACE_GATES`).
When it is `false`, every device shares and the rules below about private
places do not apply. A stock KOReader plugin reads only the status code, so
none of these fields affect it.

### Identifying your device

Send these two headers on every KOSync request (auth, progress and
annotations):

| Header | Value |
|---|---|
| `X-CWNG-Device-ID` | A stable id you generate once per install: printable, up to 100 characters. The server stores only a keyed hash of it. |
| `X-CWNG-Device-Name` | What the account owner sees on the Devices page, for example `Reader 1`. |

The first progress `PUT` with the header registers the device on the account,
with Share my place **off**. The owner can turn it on per device on the Devices
page. A `GET` never registers a device, so read-only use leaves no trace.

The header changes how your pushes are judged, and this is the part most worth
knowing:

| | No header (plain KOSync) | With `X-CWNG-Device-ID` |
|---|---|---|
| Who wins | The **furthest** place across all devices. A backward move is dropped. | The **latest** report by the client's clock, so a backward move (re-reading a chapter) lands. |
| Share my place | Not honoured: the device always shares. | Honoured per device. A device that is not sharing keeps its own place, and `GET` returns that place to it. |
| Clock | Ignored. | The `timestamp` in the `PUT` body, in Unix seconds, is used as the time of the report. One missing, before 2000, or more than five minutes ahead of the server is replaced by the time the server received it. |

The latest-wins rule applies only when the book is matched in the library; a
push for a document the server cannot match stays on the plain path. A
headerless push whose body `device_id` names a reader that was earlier seen
with the header is treated as that reader (this is how KOReader's built-in
Progress sync and our plugin share one device).

An account holds at most 20 KOReader-protocol devices. Past that a new id is
not registered and its pushes take the plain, always-shared path; the server
logs this once. When the owner removes a device, its progress `PUT`s are
refused until they restore it:

```jsonc
// HTTP 409
{ "error": "device_inactive",
  "message": "This device was removed from the account, so it no longer syncs" }
```

Stop pushing and tell the user; retrying does not help.

### Writing a position: `PUT /kosync/syncs/progress`

A client with an engine locator sends the stock KOSync body (`document`,
`progress`, `percentage`, `device`, optional `device_id`). A client without
one, such as a text-to-speech player or a plain-text renderer, says so with
`position_kind: "percentage"` and leaves `progress` out. It may add an
`anchor`, the words at its place:

```bash
curl -s -u alice:APP_PASSWORD -X PUT \
     -H 'Content-Type: application/json' \
     -H 'X-CWNG-Device-ID: 3f2a9c1e-reader-1' -H 'X-CWNG-Device-Name: Reader 1' \
     -d '{"document": "221", "position_kind": "percentage", "percentage": 0.42,
          "device": "Reader 1", "timestamp": 1794175156,
          "anchor": {"text": "lighthouse",
                     "before": "she walked down to the old",
                     "after": "and waited for the boat"}}' \
     https://cwng.example/kosync/syncs/progress
```

- `anchor.text` is required and is the word or words at the position;
  `before` and `after` are optional context, up to 8 words each is plenty.
  Limits: 200 characters for `text`, 600 for each context field. A longer
  field is refused with a 400, not clipped.
- `anchor` without `position_kind: "percentage"` is a 400, and so is a
  `progress` value alongside it.
- The server looks for the words in the library EPUB, ignoring case, accents,
  whitespace and quote or dash style. When it finds them, the place is stored
  as a locator in that EPUB, so KOReader, the web reader and a Kobo land on the
  same sentence, and the stored percentage becomes where those words are in
  the EPUB. Your percentage then only chooses between repeats of the same
  words. When they are not found, your percentage is stored as is.
- `"document": "221"` uses the `book_id_document` capability; the KOReader
  digest works too.

To read the place back as words, ask for the `anchor` kind:

```bash
curl -s -u alice:APP_PASSWORD -H 'X-CWNG-Device-ID: 3f2a9c1e-reader-1' \
     'https://cwng.example/kosync/syncs/progress/221?position_kinds=locator,percentage,anchor'
```

```jsonc
{ "document": "221", "progress": "/body/DocFragment[10]/body/div/p[4]/text().19",
  "position_kind": "locator", "percentage": 0.3972, "device": "Reader 1",
  "timestamp": 1794175300,
  "anchor": { "text": "lighthouse", "before": "…", "after": "…" } }
```

Here the anchor pushed above was found, so the place is a locator and
`percentage` is where those words sit in the EPUB, not the 0.42 that was sent.
`anchor` is present only when the server could turn the winning place into
words in the library EPUB and the account may read that book. A device that
is not sharing gets its own place back as a percentage, without an
`anchor`.

### Highlights and notes: `/kosync/syncs/annotations`

```
GET /kosync/syncs/annotations/<document>[?text_quote=1]   server -> device
PUT /kosync/syncs/annotations                             device -> server
```

Each annotation travels as one portable object (`annotation_id`, `type`,
`highlighted_text`, `note_text`, `color`, `hidden`, `source`, the anchor
fields, and `text_quote`). Send the device headers on both.

**Pull.** The reply always has `book_known`:

```jsonc
{ "document": "…", "calibre_book_id": 221, "book_known": true,
  "annotations": [ … ], "annotation_count": 3,
  "foreign_annotation_ids": ["…"] }   // only when X-CWNG-Device-ID names a known device
```

`book_known: false` with an empty list means the server does not know this
book, not that it has no highlights. Never delete local highlights to match
that reply. If `book_known` is absent you are talking to an older server: be
just as careful. Hidden rows are included so you can mirror a deletion made
elsewhere. With `?text_quote=1`, a highlight whose words could be found in the
library EPUB also carries `text_quote: {"exact", "prefix", "suffix"}`.

**Push.** A client without an engine anchor names each highlight by its words:

```bash
curl -s -u alice:APP_PASSWORD -X PUT -H 'Content-Type: application/json' \
     -H 'X-CWNG-Device-ID: 3f2a9c1e-reader-1' -H 'X-CWNG-Device-Name: Reader 1' \
     -d '{"document": "221", "device_id": "3f2a9c1e-reader-1", "device": "Reader 1",
          "delete_source": "textquote",
          "annotations": [{"annotation_id": "reader1-0007", "type": "highlight",
                           "source": "textquote", "percentage": 0.42,
                           "text_quote": {"exact": "the old lighthouse",
                                          "prefix": "she walked down to",
                                          "suffix": "and waited"}}],
          "deleted": ["reader1-0003"]}' \
     https://cwng.example/kosync/syncs/annotations
```

```jsonc
{ "document": "221", "calibre_book_id": 221, "matched": true,
  "created": 1, "updated": 0, "deleted": 0, "unchanged": 0, "skipped": 0,
  "resolved": ["reader1-0007"], "unresolved": [], "reconciled": true }
```

`reconciled: true` means the push named deletions. `deleted` counts the rows
actually hidden, so naming an id the server never had, or one already
deleted, reports `deleted: 0`.

- `annotation_id` is yours to choose and must stay stable for that highlight.
- A quote found in the library EPUB is stored as a KOReader locator range and
  listed in `resolved`; one not found is kept with the quote as its only anchor
  and listed in `unresolved`. `percentage` (0 to 1) only picks between repeats.
  `exact` may be up to 4000 characters.
- **Deletions are named, never inferred.** List the ids the user deleted in
  `deleted`. Leaving a highlight out of `annotations` deletes nothing.
  `delete_source` says whose rows you may delete: `koreader` (the default) or
  `textquote`. A push can never delete a Kobo or web-reader highlight, or one
  another identified device made.
- `matched: false` (still HTTP 200) means the server could not match the book
  and **saved nothing**. Treat it as a failure to report, not a success.
- The device that made a highlight is recorded, so its own rows are not echoed
  back as someone else's; `foreign_annotation_ids` on pull lists rows other
  devices made.

## Routes

Every route the `kosync` blueprint serves. Unless a row says otherwise, a
route answers 503 while KOReader sync is switched off and authenticates with
HTTP Basic (an app password works). Rows marked *device* also need the
device's identity: the `X-CWNG-Device-ID` and `X-CWNG-Device-Name` headers,
or `device` and `device_id` in the JSON body for the inventory, delivery,
deletion and collection calls that send one. `tests/unit/test_progress_syncing_readme_routes.py` fails
when this table and the served routes disagree.

| Method | Path | What it does |
|---|---|---|
| `GET` | `/kosync` | Web page with the plugin download and setup steps. Not an API call: no Basic auth, and it still loads when sync is off. |
| `GET` | `/kosync/users/auth` | Checks credentials; the JSON lists `capabilities` (see Feature detection). |
| `GET` | `/kosync/syncs/progress/<document>` | Reading position for a document digest. |
| `PUT` | `/kosync/syncs/progress` | Stores a reading position; the reply says whether it was accepted. |
| `GET` | `/kosync/export` | All of the user's reading progress as JSON, with titles, authors and identifiers. Percentages are 0-100, not 0-1. |
| `GET` | `/kosync/syncs/annotations/<document>` | Highlights and notes for a book, server to device. |
| `PUT` | `/kosync/syncs/annotations` | Highlights and notes the device made, changed or deleted. |
| `GET` | `/kosync/syncs/library` | The books this account's e-readers should hold, one page at a time. |
| `GET` | `/kosync/syncs/library/books/<book_id>/placeholder` | Small EPUB that stands in for a book not downloaded yet. |
| `GET` | `/kosync/syncs/library/books/<book_id>/file` | The book file, in the format KOReader reads best. |
| `PUT` | `/kosync/syncs/read_status` | Unread, reading or finished, as chosen on the device. |
| `PUT` | `/kosync/syncs/inventory` | *device* The complete list of books on the device. Nothing is treated as deleted because it is missing. |
| `POST` | `/kosync/syncs/deliveries/claim` | *device* Leases the oldest book waiting for this device. |
| `PUT` | `/kosync/syncs/deliveries/refuse` | *device* Gives a lease back, for example when the device is out of space. |
| `GET` | `/kosync/syncs/deliveries/<delivery_id>/download` | *device* (headers, plus the lease's `X-CWNG-Claim-Token`) Downloads exactly the leased file. Needs the download permission. |
| `PUT` | `/kosync/syncs/deliveries/complete` | *device* Confirms a delivered file is installed. Safe to repeat. |
| `POST` | `/kosync/syncs/deletions/claim` | *device* Leases the next book the owner asked to remove from this device. |
| `PUT` | `/kosync/syncs/deletions/complete` | *device* Reports whether that removal happened. |
| `POST` | `/kosync/syncs/collections` | *device* Collections this device should show. |
| `PUT` | `/kosync/syncs/collections/complete` | *device* Confirms the collections were applied. |
| `PUT` | `/kosync/syncs/sleep-screen/device` | *device* (headers) Reports the device's screen so a sleep screen can be made for it. |
| `GET` | `/kosync/syncs/sleep-screen` | *device* (headers) The current sleep-screen manifest. |
| `GET` | `/kosync/syncs/sleep-screen/image` | *device* (headers) The sleep-screen image. |
| `POST` | `/kosync/syncs/sleep-screen/ack` | *device* (headers) Confirms the device shows that image. |
| `POST` | `/kosync/pair/start` | Starts pairing with a code. No credentials (the device has none yet); rate-limited per network. |
| `POST` | `/kosync/pair/poll` | Polls a pairing code; returns credentials once, after the owner approves. No credentials needed. |

## Testing

```bash
# Unit tests - Algorithm, storage, database
pytest tests/unit/test_progress_syncing_*.py -v

# Integration tests - Full sync workflow
pytest tests/integration/test_progress_syncing_*.py -v
pytest tests/integration/test_kosync_*.py -v
```

## Extending

### New Sync Protocol

```python
# protocols/new_protocol.py
from flask import Blueprint, request
from .kosync import get_book_by_checksum

new_protocol = Blueprint('new_protocol', __name__)

@new_protocol.route('/new_protocol/sync', methods=['PUT'])
def sync():
    checksum = request.json['document']
    book_id, format, title, path, version = get_book_by_checksum(checksum)
    # Implement sync logic
```

Register in `cps/main.py`:
```python
from .progress_syncing.protocols.new_protocol import new_protocol
app.register_blueprint(new_protocol)
```

### New Checksum Algorithm

```python
# checksums/new_algorithm.py
def calculate_new_algorithm(filepath: str) -> str:
    # Implementation
    return checksum

# Store alongside existing
from .manager import store_checksum
store_checksum(book_id=123, book_format='EPUB',
               checksum=new_checksum, version='algorithm_v2')
```

Database supports multiple algorithm versions per book automatically.

## References

- [KOReader partialMD5](https://github.com/koreader/koreader/blob/master/frontend/util.lua#L1107)
- [KOSync protocol](https://github.com/koreader/koreader-sync-server)
- CWA plugin: `koreader/plugins/cwngsync.koplugin/`
