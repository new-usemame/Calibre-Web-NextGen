### Fixed

- **Test-suite reliability: parallel test workers no longer collide on the
  shared temp directory.** Several maintenance scripts guard themselves with a
  one-at-a-time lock file in the system temp directory, which is correct when
  the app runs but means parallel test workers were fighting over the same lock
  and reporting each other's losses as failures. Each worker now gets its own
  temp directory, so those runs are stable.
