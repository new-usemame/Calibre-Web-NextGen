### Fixed

- **A highlight that fails to save no longer looks saved.** If the database
  rejected the write, the web reader still showed the highlight as created and
  a delete still reported success — the change was gone but nothing said so.
  Those paths now report the failure, and KOReader is told its push did not
  land instead of being acknowledged.
