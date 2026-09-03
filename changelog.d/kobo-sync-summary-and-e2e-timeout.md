### Fixed

- **Kobo sync summaries now distinguish held books from removal replays.**
  `suppressed_replay` reports every same-reader fingerprint replay kept off the
  wire, while `suppressed_unchanged` reports only the unchanged held-book
  subset. The E2E gate also caches its version-matched Playwright browsers and
  stops a stalled browser installation after three minutes so it can be rerun
  promptly.
