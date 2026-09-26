### Fixed

- **A settings page that fails validation no longer undoes a background
  task's progress.** Reloading settings after a rejected change now reads
  what is stored, so a finished KEPUB repair or backfill is not scheduled to
  run again in the same session.
