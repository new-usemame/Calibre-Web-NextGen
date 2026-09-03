### Fixed

- **Kobo sync now preserves deletion progress when no archive changes are
  pending.** Empty archive passes no longer reset the reader's deletion cursor
  and make previously consumed tombstones eligible again on alternate syncs.
  A deletion missing from that physical reader's acknowledgment ledger is
  still announced even when its timestamp is behind the reader's cursor.
