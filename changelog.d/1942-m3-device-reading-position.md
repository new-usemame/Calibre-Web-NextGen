### Added

- **Reading positions are now retained per Kobo and browser device.** A
  re-downloaded Kobo book receives its resolved reading state on a subsequent,
  bounded sync page even when the normal reading-state cursor is already
  ahead. The response which offers replacement bytes only arms the repair;
  byte-identical entitlement replays remain suppressed without re-arming it.

### Fixed

- **A fresh-download cover reset can no longer overwrite a real cross-device
  position.** Device observations remain independently inspectable, resolved
  progress suppresses only an armed near-cover reset, intentional newer
  backward jumps still reach the resolved row and external progress carriers,
  and status and reading statistics use the newest valid device timestamp.
- **Kobo sync response state is committed atomically.** Shelf tombstones,
  entitlement fingerprints, synced-book markers, and position repair latches
  now share the request's one checked commit, so a failed response remains
  fully retryable.
