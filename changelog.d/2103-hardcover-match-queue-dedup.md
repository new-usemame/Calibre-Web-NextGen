### Fixed

- **Hardcover auto-fetch no longer repeats ambiguous work indefinitely.** Each book now has at
  most one pending match-review item, refreshed with the newest candidates instead of duplicated.
  Books whose match was rejected are excluded before Hardcover is searched again, and upgrades
  collapse existing duplicate pending items while preserving reviewed history. Reported by
  @magdalar in #2103.
