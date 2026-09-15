### Fixed

- **Upgrading no longer tells an existing Kobo that its whole library is
  new.** The one-time delivery-ledger audit used to throw away everything the
  server knew about what a reader already held, so the first sync after the
  update re-announced every book and the reader re-downloaded the lot and lost
  its place in each one. A reader that is the only one paired to an account now
  keeps that record, and the upgrade sync is silent. Accounts with more than one
  paired reader are unchanged, because there the old record cannot tell the two
  readers apart.
