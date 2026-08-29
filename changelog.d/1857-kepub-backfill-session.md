### Fixed

- **One failed Kobo KEPUB conversion no longer prevents every later synced book
  from being converted.** The startup backfill now rolls back and replaces a
  failed database session between books, validates rebuilt sessions against the
  real Calibre metadata schema, and stops after three repeated database or
  recovery failures instead of flooding the log for the rest of the library.
  Failed/aborted runs now preserve exact processed/failed counts and remain
  marked incomplete. Reported by @MKos75 and @Tobi.
