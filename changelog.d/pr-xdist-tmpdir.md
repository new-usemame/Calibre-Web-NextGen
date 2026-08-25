### Fixed

- **Test-suite reliability: concurrent test processes no longer fight over the
  same lock file.** Several maintenance scripts guard themselves with a
  one-at-a-time lock in the system temp directory, which is correct when the app
  runs but meant any second process on the machine — a parallel test worker, a
  second test run, or the app itself — could make unrelated tests report
  failures. Each test process now gets its own temp directory.
