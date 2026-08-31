### Fixed

- **Backend concurrency changes can no longer merge behind a frontend-only test gate.** CI now runs the
  full browser suite against the triggering commit's immutable container digest when database-engine or
  concurrent request-handling code changes, instead of accidentally testing the previous `:dev` image.
