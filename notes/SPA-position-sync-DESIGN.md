# SPA position sync — coordinated SPA + SPB phase design

## Status and boundary

Position save/restore is deliberately excluded from the Phase 1 appearance-settings shipment. The current web reader continues to use its pre-existing private EPUB CFI bookmark behavior; this phase does not read or write `KOSyncProgress` and does not change `cps/progress_syncing/`.

This follow-up must be implemented as a coordinated SPA + SPB change because the shared KOReader record is a protocol boundary. Terra's adversarial review in `notes/org/SPA-REPORT/terra/REPORT.md` found five release-blocking failure classes in the interrupted prototype (F1–F5).

## Cross-engine contract

- The only common position carrier between epub.js and KOReader is a normalized percentage.
- `KOSyncProgress.progress` remains KOReader-native. An EPUB CFI must never be stored in or returned through that field (Terra F1).
- epub.js exact positions remain private EPUB CFIs in a dedicated web-position store. KOReader exact locators remain private to the KOSync record.
- A restore response may expose the winning normalized percentage plus an engine-compatible exact locator only when one exists for that engine. It must never label a foreign locator as compatible.

## Storage and migration

Introduce a dedicated web-reader position table rather than extending `Bookmark` or overloading `KOSyncProgress`. At minimum it needs:

- `user_id`, canonical `document`, EPUB format/edition identity, private `epub_cfi`, normalized percentage, device id, monotonic revision, and updated timestamp;
- a database uniqueness constraint on `(user_id, document)` for the shared percentage owner (Terra F3);
- any per-format/per-edition exact-locator uniqueness needed to prevent one EPUB's CFI being restored into another edition;
- an explicit migration and duplicate-resolution policy before adding the constraint.

SPB must own or approve canonical document/checksum alias resolution. Numeric book ids and registered checksums must resolve to one logical document without creating concurrent first-writer duplicates.

## Atomic, ordered save protocol

The client submits a complete body: document identity, percentage, private CFI, device id, and expected revision/new revision. The server must:

1. Parse and validate the entire JSON object, types, finite percentage range, locator grammar, identity, and revision before mutating any row (Terra F4).
2. Begin one transaction and lock/select the unique logical position row, or perform a single compare-and-set update guarded by its revision.
3. Reject stale revisions regardless of network arrival order. A delayed relocation request cannot overwrite a later pagehide save (Terra F3).
4. Apply the cross-device winner policy to the normalized percentage and update the private web CFI only when that web write wins. A rejected lower cross-device write must not replace the durable web restore locator (Terra F5).
5. Commit both the shared percentage decision and private web locator atomically, then return the accepted revision and winning percentage.

Deliberate rewind semantics need an explicit operation or CAS against the current revision; device id alone is not evidence that an out-of-order lower write is intentional.

## Restore ordering in epub.js

Do not save or reconcile percentage until `book.locations.generate()` has completed and the displayed CFI can be converted consistently. The restore sequence is:

1. Fetch the coordinated restore snapshot and appearance settings.
2. Create the rendition and generate locations.
3. Resolve the winning normalized percentage to a web CFI only after locations are ready; prefer a validated stored CFI when it belongs to this EPUB edition.
4. Display the resolved target.
5. Read back the rendition's actual CFI and percentage together, assign both refs/state atomically, and only then enable relocation/pagehide saves (Terra F2).

If location generation or percentage-to-CFI conversion fails, preserve the last validated private CFI and disable percentage reconciliation/saves for that session. Never silently pair an old CFI with a newly displayed percentage.

## Required verification before shipment

- Real DB + authenticated HTTP: KOReader checksum-keyed write → web restore/save → KOReader GET never exchanges incompatible locator grammars (F1).
- Invalid JSON, arrays, missing paired fields, invalid CFI, non-finite/out-of-range percentage, and stale revision leave every store byte-for-byte unchanged (F4).
- Initial display before locations-ready followed immediately by background/pagehide cannot write a mismatched CFI/percentage (F2).
- Reversed completion order for relocation and pagehide requests preserves the higher revision; concurrent first writers yield exactly one logical row (F3).
- Lower cross-device save cannot replace either the winning percentage or the durable web CFI; same-device deliberate rewind succeeds only through the explicit revision contract (F5).
- Checksum aliases and numeric book identity converge on the unique logical document.
- Browser verification covers reload restore, tab close/background, two browser devices, KOReader interop, and location-generation failure.

Until all links above are observed, position sync remains a Phase 2 gap and must not be described as verified or shipped.
