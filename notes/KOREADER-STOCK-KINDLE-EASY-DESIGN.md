# KOReader, as easy as a stock Kindle — design

Status: in build (KINDLE KIDS 2, 2026-09-23). Owner decisions quoted from the operator.
Builds on `notes/CWNGSYNC-CALIBRE-CAPABILITIES-DESIGN.md` (inventory, delivery, collections).

## The promise

Pick up a KOReader device, see your CWNG library as covers, tap a book, read. Position, read status
and highlights follow you without a "sync" button. A book sent from the website shows up. Setup is
one step. Nobody needs to learn KOReader's file manager, OPDS dialogs or plugin menus.

Operator, on the library: *"Should be just like the kobo integration … it shows the user's library
if they want whole library sync or respects their preference otherwise."*
Operator, on setup: *"at least 2 ways that are the least amount of friction and have different
requirements."*

## What is wrong today (measured 2026-09-23, KOReader v2026.07.1 on a KT6 Kindle)

1. KOReader opens on `/mnt/us`: `audible/ fonts/ kmc/ libkh/ lost+found/` — system folders, no books.
2. Plugin install is a manual unzip; the menu lives under Tools as "NextGen Progress Sync".
3. Setup = type a URL and a password on an e-ink keyboard; the password is stored in plain text.
4. Every automatic behaviour is off by default (`auto_sync=false`, highlights opt-in and manual), and
   turning auto-sync on is refused until the user changes a KOReader network setting.
5. No way to see the library on the device except KOReader's OPDS browser (text list, 3+ taps).
6. Sent books only arrive while a book is open or via a menu item; they land flat in the home folder.

## Shape

### 1. The library is a folder of real covers — cloud books are tiny placeholder EPUBs

The device keeps one folder, the **CWNG library folder**, containing one EPUB per book in the user's
e-reader scope. A book not yet downloaded is a **placeholder EPUB** (~20–40 KB: the cover with a
small cloud badge, the metadata, one explanatory page). KOReader's own CoverBrowser mosaic renders
the folder as a cover grid — sorting, paging, search, collections and progress bars all come from
KOReader itself, so we ship no custom grid widget to maintain.

Tapping a placeholder never opens it: the plugin intercepts the open, downloads the real book over the
placeholder's path (atomic temp-file + rename, checksum-verified, reusing `delivery.lua`'s rules),
drops the cached cover so the grid shows the real one, and opens the real book. If the placeholder
is ever opened some other way, it shows one page saying so, and the plugin's ReaderReady fallback
closes it and runs the same download.

Why placeholders over a custom home screen: they reuse KOReader's mature, fast grid; they survive
KOReader updates better than a custom widget; the file-level model is what delivery, inventory and
collections already speak. Cost: a small file per book (225 books ≈ 7 MB).

Folder: Kindle `/mnt/us/cwng-library` (outside `documents/`, which the stock Kindle indexes; the
stock side is deliberately untouched). Kobo `/mnt/onboard/.cwng-library` (dot-folder so Nickel does
not index the placeholders). Elsewhere `<home_dir>/CWNG Library`.

### 2. Scope = the preference the user already has for Kobo

The books in the folder are exactly the books Kobo sync would give that user — **one preference,
honoured by every e-reader**:

- `user.kobo_only_shelves_sync` off → the whole library the user can see (content restrictions and
  personal *My Library* membership apply; archived books excluded).
- on → only books on the user's shelves marked for e-reader sync (`shelf.kobo_sync`) plus magic
  shelves marked `kobo_sync`, intersected with *My Library* when that is on.

The rule is computed by ONE server function used by both the Kobo sync and the KOReader library
(`cps/services/ereader_scope.py`), never re-derived in a second place. User-facing wording moves
from "Kobo" to "e-readers" where the setting is shown.

Leaving scope removes the placeholder. A downloaded book that leaves scope is removed only when its
bytes still match what the server sent (unmodified) and it is not open — Kobo parity (Kobo archives
books that leave scope), with the existing checksum guard. Its sidecar (position, highlights) stays,
so if the book returns it resumes.

### 3. Sync without a button, and without Wi-Fi nags

Measured KOReader facts that drive this: on Kindle, KOReader never switches Wi-Fi on suspend
(`hasWifiManager` false); `isOnline()` needs public DNS and fails on an internet-less LAN, so use
`isConnected()`; the "turn Wi-Fi on first" helpers prompt or pop up and must never run from
background events.

- Progress: push on page-turn idle, close and suspend **only when connected**; otherwise append to an
  offline queue; flush the queue and pull on NetworkConnected, Resume and ReaderReady. Forward jumps
  apply silently (with a toast); backward jumps never happen automatically.
- Read status: the server already maps ≥99% to finished. The plugin also pushes an explicit status
  when the user marks a book finished/unread in KOReader, and applies the server's read status to
  every book's sidecar so the grid shows it.
- Highlights: on by default; pushed on close/suspend/idle. (Server→KOReader highlight write-back is
  a later phase; it needs position translation for highlights made elsewhere.)
- Library + deliveries: refreshed on NetworkConnected, Resume, FileManager show (throttled), and
  after setup. Deliveries ("send to device") download the real book straight into the library folder
  at its library filename, so a sent book is a normal, already-downloaded cover in the grid.

### 4. Two setup routes with different requirements, plus the manual fallback

| Route | Needs | What the user does |
|---|---|---|
| **Ready-made plugin** | a computer + USB | Website → Devices → Add e-reader → KOReader → *Download ready-made plugin*. Unzip into `koreader/plugins/`, restart KOReader. The zip carries the server address and a fresh app password; the plugin imports it on first start, deletes the file, and the library appears. Zero typing on the device. |
| **Pair with a code** | the device on Wi-Fi + any browser signed in to CWNG | The plugin's first-run screen shows `Go to <server>/devices and enter K7M4-QX2P`. The user types that on a phone; the device receives its own app password and continues. Zero typing on e-ink when the plugin came from the server (address pre-filled); otherwise the address is typed once. |
| Manual (existing) | nothing | Type address + username + password or app password. |

Credentials a device holds are always app passwords, so they are revocable per device and never the
account password.

### 5. Defaults applied once, on successful setup

`auto_sync=true`, `sync_forward=SILENT`, `sync_backward=DISABLE`, `sync_annotations=true`; KOReader:
`home_dir=<library>`, `lock_home_folder=true`, `start_with=filemanager`, CoverBrowser
`filemanager_display_mode=mosaic_image`, collate by last-read. Applied once (flag), never re-imposed
over a later user change.

## Server contract (all under `/kosync`, Basic auth as today unless marked public)

Device identity travels as JSON body fields `device`/`device_id` on POST/PUT, and as headers
`X-CWNG-Device-Name` / `X-CWNG-Device-ID` on GET (as the delivery download already does).

- `GET /syncs/library?cursor=&limit=` → `{revision, scope: "library"|"shelves", scope_shelves:
  [{id,name}], total, next_cursor, books: [{book_id, title, authors[], series, series_index,
  filename, format, size, checksum, rev, read_status: "unread"|"reading"|"finished", progress,
  shelves[], added, last_read}]}`. `?if_revision=<r>` → `{unchanged:true, revision}` when nothing in
  scope, metadata, cover or read status changed. `filename` = the delivery naming rule
  (`device_delivery._delivery_filename`) with the chosen format's extension; `checksum` = KOReader
  partial MD5 of the file `/file` would serve, when known.
- `GET /syncs/library/books/<id>/placeholder` → `application/epub+zip`, header
  `X-CWNG-Placeholder-Rev`. OPF carries `<meta name="cwng:placeholder" content="<id>"/>` and the
  file contains `META-INF/cwng-placeholder.json` `{book_id, rev}`. 404 outside the user's visibility.
- `GET /syncs/library/books/<id>/file` → the KOReader format bytes; `Content-Length`,
  `X-CWNG-Checksum`, `X-CWNG-Filename`. Requires download permission and visibility
  (`get_filtered_book(..., user=user)`); registers the device.
- `PUT /syncs/read_status` `{device, device_id, book_id | document, status}`.
- Pairing (public, rate-limited): `POST /pair/start {device, device_id}` → `{user_code,
  device_code, expires_in, interval, verify_url}`; `POST /pair/poll {device_code}` →
  `{status:"pending"|"denied"|"expired"}` or, exactly once, `{status:"approved", server, username,
  password}` (the app password is minted at claim time; no cleartext at rest).
- Web (session + CSRF): `GET /api/v1/devices/koreader/pair/<user_code>` (who is asking: device name,
  requested at, IP); `POST …/approve`, `POST …/deny`; `POST /api/v1/devices/koreader/setup-bundle` →
  zip of `cwngsync.koplugin/` + `setup.json {server, username, password}`.

## Build order and proof

1. Server contract + scope service + pairing + bundle (tests: scope parity with Kobo in both modes,
   placeholder is a valid EPUB KOReader's CoverBrowser reads, visibility/permission refusals, pairing
   expiry/once-only/rate limit, bundle credential revocable).
2. Plugin: setup (bundle import, pairing, manual), defaults, library engine (pure diff module with
   host-Lua tests), open intercept, offline queue, automatic highlights/read status.
3. Web: Devices → Add e-reader → KOReader: the two routes; e-reader scope wording.
4. Proof: KOReader emulator against the dev instance, then the real Kindle (KT6) against the household
   instance — every "done" item OBSERVED on the device, with screenshots from its framebuffer.
5. Guide: `docs/koreader-kindle-guide.md` (what to tap, what happens by itself, what to do when not).

## Deliberately deferred

- Server→KOReader highlight write-back for highlights made on other readers (position translation).
- Plugin self-update from the user's server (today: Updates Manager / re-download).
- Per-device scope override (today one preference for all e-readers).
