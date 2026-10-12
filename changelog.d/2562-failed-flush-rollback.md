### Fixed

- **The web UI recovers after a database-locked error instead of failing every request.** A failed flush (e.g. `sqlite3.OperationalError: database is locked`) left the shared app.db session in a `PendingRollbackError` state, so every later request failed until restart. The request-teardown handler now rolls the app.db session back when a failed flush has deactivated its transaction, so the next request starts clean. Reported by @mike in #2562.
