### Fixed

- **The parallel unit suite now tears down Kobo recovery-retention workers
  deterministically.** A retention timer or startup sweep that had already
  begun could outlive its test, contend on shared locks, and reschedule itself
  after the test fixture only cancelled its registered timer. Teardown now
  invalidates that maintenance generation and joins every timer and startup
  thread before the next test starts. Translation-context tests also compile
  into test-owned temporary storage, so a clean run no longer changes how many
  tests execute on the following run (#1868).
