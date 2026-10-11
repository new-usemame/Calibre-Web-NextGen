### Changed

- **Kobo annotation requests no longer re-inspect the database schema every time.** The Stage 0 capability check now remembers its answer per database and re-checks only when SQLite reports a schema change (a migration, or a dropped trigger or index), so busy Kobo sync paths skip dozens of introspection queries per request while still failing closed if the schema regresses. Closes #2547.
