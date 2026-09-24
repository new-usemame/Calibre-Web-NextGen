### Fixed

- **The server log no longer says KOReader highlights were lost when they were
  only sent again.** KOReader sends a book's highlights each time it syncs, and
  the ones the server already had were logged as "NOT stored", a warning on
  every book opened. They are now counted as unchanged, and the warning is kept
  for highlights the server really did not store.
