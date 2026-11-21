### Fixed

- **KOReader sync can now identify a possible damaged checksum index when a book lookup fails.** When an indexed lookup misses a row that a table scan can find, the log names the affected book and suggests running `PRAGMA integrity_check` on a consistent copy of the library database. Thanks to @sgreadly and @FormelLMS (#2960).
