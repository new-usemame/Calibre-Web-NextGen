### Fixed

- **Kobo sync confirmations survive long periods offline.** Returning after
  more than seven days now records the final page as delivered instead of
  sending its books again and risking a Nickel re-download.
