### Fixed

- **Skipped tests are named in CI instead of disappearing into a count.** Fast
  and Docker test logs now list every skipped test and reason, and regressions
  that break first-party modules fail instead of being mistaken for missing
  optional dependencies.
