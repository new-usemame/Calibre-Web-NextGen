### Fixed

- **Kobo upgrades retain recorded per-device deliveries.** The one-time delivery-ledger audit used to throw away everything the
  server knew about what a reader already held, so the first sync after the
  update re-announced every book and the reader re-downloaded the lot and lost
  its place in each one. The upgrade now retains each reader's own recorded
  deliveries, including accounts with several readers or a retired device.
  Shared historical guesses are distinguished from later device-specific
  delivery records, so a newly paired reader still receives its first copy.
  Outstanding book deletions that an older upgrade marked as sent without
  actually sending them are also delivered correctly.

  Installations older than v4.1.43 did not record per-device delivery history.
  Their first sync can still reannounce previously downloaded books; this fix
  cannot reconstruct that missing history.
