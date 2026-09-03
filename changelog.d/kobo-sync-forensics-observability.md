### Changed

- **Kobo sync logs now explain every entitlement response without exposing a
  device identifier.** One structured INFO line records the short device hash,
  incoming and outgoing cursors, new, changed, removed, and replay-suppressed
  counts, and why any book already in the device ledger was re-emitted. This
  makes an unexpected download loss diagnosable without a raw traffic capture.
  When the existing private Kobo exchange capture is explicitly enabled,
  library-sync requests now use the same bounded, rotating store as annotation
  exchanges to retain the exact response body and opaque cursors, a hashed
  device label, and a link to those INFO counters.
