### Fixed

- **Slow storage no longer discards Kobo annotation recovery bodies.** If a
  durable spool write outlasts the request deadline, it now finishes in the
  background, and one following PATCH can also be admitted instead of being
  rejected merely because the first write is still completing.
