### Fixed

- **Hardcover's automatic ID crawler can now be turned off on its own.** The Run Schedule on the
  CWA settings page has a `Never (auto-fetch off)` option, so the crawler stops without disabling
  Hardcover reading-progress or annotation sync — previously the only off switch was Enable
  Hardcover Sync, which turned off all three. Reported by @magdalar.
- **An unrecognized Hardcover auto-fetch schedule is now diagnosed instead of silently doing
  nothing.** A stored value the scheduler does not recognize is logged and falls back to the weekly
  default, and the settings page refuses to persist an unrecognized value in place of the one you
  already had.
