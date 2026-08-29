# cwngsync: bringing Calibre desktop's device capabilities to CWNG

Status: **design, not yet implemented** · Opened 2026-08-28

## Thesis

Calibre desktop can do things to an e-reader that CWNG cannot: it knows what is
physically on the device, pushes books to it, deletes from it, and reads its free
space. OPDS structurally cannot express any of that — it is a pull-only catalog
and the server never learns what the client did.

The obvious way to get those capabilities is to implement Calibre's **smart device
protocol** (JSON-over-TCP, 21 opcodes, UDP broadcast discovery on
`54982/48123/39001/44044/59678`, `<decimal-length><json>` framing, shared-password
auth). **We are deliberately not doing that first.** Two reasons:

1. **It is LAN-only and session-bound.** The device connects only while a human
   taps *Connect*, and discovery is UDP broadcast. "The server knows what is on the
   device" would be true only during an active session — for a device asleep 99% of
   the time that is a much weaker guarantee than it sounds, and it never works over
   the tunnel or on cellular.
2. **It would add a listening TCP socket to an image with ~196K pulls**, authenticated
   by a single shared password with no per-user notion. That is a security surface for
   every CWNG user to buy a feature for a few.

Instead we implement **the capabilities, not the protocol**, over the authenticated
HTTP channel we already own: `cwngsync.koplugin` (today `cwasync.koplugin`). It
already speaks per-user-authenticated HTTP to CWNG, already works remotely through a
reverse proxy, already registers a device identity, and already has a wire-contract
test. Interop with the calibre ecosystem stays available as a later opt-in (Phase 5),
where it is a real feature rather than an accident of design.

## Constraints that bind every phase

- **`api.json` governs the wire twice.** lua-Spore rebuilds the request body from
  `payload` *and* validates caller params against `required_params`/`optional_params`.
  A field declared in one and not the other fails — silently in one direction (#906),
  loudly in the other (#924). `tests/unit/test_cwasync_plugin_wire_contract.py`
  enforces both. **Every new field lands in all the right lists or it does not ship.**
- **The plugin is versioned in lockstep with the CWNG release tag** (`_meta.lua`
  `version`, read by KOReader's Updates Manager). `test_cwasync_plugin_version_bump_gate.py`
  gates this.
- **The plugin is device-agnostic**, with providers for Kobo (`kobo_sqlite_provider.lua`)
  and KOReader (`koreader_annotations_provider.lua`). New capabilities must not assume
  a Kindle, or they break Maggie's Libra.
- **Deletions are named, never inferred.** Established by #905/#906: a push declaring
  itself complete must not let the server reap rows it omitted. Inventory reporting
  must not become an implicit delete channel.

## Phase 0 — rename `cwasync` → `cwngsync`

**The hazard, and it is real.** `_meta.lua` declares `name = "cwasync"`. That string is
the plugin's identity to KOReader. Renaming the directory and the name produces a
*different* plugin, so a user who installs the new one without removing the old ends up
with **both loaded, both syncing the same books** — duplicate pushes and a conflict
source. A rename is therefore a migration, not a `git mv`.

Required:

1. New `cwngsync.koplugin` detects a loaded/installed `cwasync.koplugin` and
   **refuses to run** (with a clear on-device message) rather than double-syncing.
2. Settings migrate. Audit which keys are plugin-scoped vs `G_reader_settings`-global
   before assuming — `device_id` in particular must survive, because it is the
   identity CWNG's device registry fingerprints.
3. A final `cwasync` release whose only job is to tell users to migrate.
4. Server-side references updated: `cps/templates/kosync_plugin.html`,
   `scripts/publish-cwasync-plugin.sh`, `cps/services/reading_position.py`,
   `cps/services/annotation_types.py`, and the four `test_cwasync_*` tests.
5. GitHub repo rename (redirects work) plus whatever the Updates Manager pins —
   `test_cwasync_updates_manager_compat.py` is the guard.

**Test first:** a test asserting the two plugins cannot both be active, and a settings
migration test with a realistic pre-rename settings fixture.

## Phase 1 — device inventory (the largest OPDS gap)

Let the server know what is actually on each device.

- Plugin enumerates its library and reports `{lpath, checksum, book_id?, size, mtime}`.
- New endpoint on the existing `kosync` blueprint, authenticated exactly as today.
- Rows attach to the `Device` from `register_koreader_device_best_effort()` — so this
  is per-device state under #1942's model, keyed by the existing HMAC fingerprint, and
  **no schema change to `Device` itself**.
- UI: an "on device" indicator, and per-device library view.

**Explicitly not** a delete channel. Inventory is an observation; it never causes the
server to remove anything.

**Test first:** inventory push with an unknown checksum; with a book that exists under
a different format checksum (the #633 convergence case); with two devices owned by
different users (must not cross-bind — `register_*_best_effort` already refuses this);
and an inventory that omits a previously-reported book (must NOT delete server rows).

## Phase 2 — send-to-device (push without a socket)

Calibre's `SEND_BOOK`, achieved by pull.

- Server maintains a per-device **wanted queue**; user marks books "send to device"
  from the web UI (or a shelf is marked auto-send).
- Plugin asks "what do you have for me?" on its existing sync cycle and downloads over
  the authenticated HTTP channel it already holds.
- Because it is pull, it works over the tunnel, on cellular, and needs no open port.
  It lands whenever the device next syncs rather than instantly — an honest and
  acceptable trade for a device that is usually asleep.

**Test first:** queue/claim/complete lifecycle; idempotent re-delivery; a book already
present per Phase 1 inventory is not re-sent; format selection honours EPUB-only
devices (KOReader cannot read KFX/AZW3).

## Phase 3 — storage awareness and server-requested delete

- Plugin reports free/total space (calibre's `FREE_SPACE`/`TOTAL_SPACE`).
- Server may *request* deletion of named books; the device performs it and confirms.
  Named, never inferred — same rule as annotations.
- Refuse to queue a send that would not fit.

## Phase 4 — shelves as device collections

Map CWNG shelves to KOReader collections so the device's library organisation mirrors
the server's, per user.

## Phase 5 — optional: the actual calibre wire protocol

Only if ecosystem interop (Calibre Companion and other smart-device clients) is wanted
for its own sake. Ships **off by default**, bind address configurable, behind its own
security review. Roughly 2.5–3.5k LOC with tests; the protocol is unusually testable
because a fake socket client exercises nearly all of it with no hardware.

## Sequencing note

Phases 1–4 are each independently shippable on the daily train and each closes a real
gap. Phase 0 must land first only because renaming after users adopt new features is
strictly worse than renaming now.
