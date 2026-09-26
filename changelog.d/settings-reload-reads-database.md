### Fixed

- **A settings page that fails validation no longer undoes a background
  task's progress.** Reloading settings after a rejected change now reads
  what is stored, so a finished KEPUB repair or backfill is not scheduled to
  run again in the same session.
- **Saving settings works again after the server recovers from a database
  error.** Once the web session had to be reset after a failed rollback, every
  admin settings save failed until the server was restarted.
