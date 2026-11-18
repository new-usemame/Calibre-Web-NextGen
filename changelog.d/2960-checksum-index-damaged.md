### Fixed

- **KOReader sync's "No book found for checksum" line now says when the cause is a damaged checksum index, and a warning is logged once.** If the checksum index in `metadata.db` has lost entries, the lookup misses a row that is in the table, and the log used to blame a file the server never served. It now retries without the index and, if the row is there, names its book id on the miss line, logs one warning per five minutes, and points at `PRAGMA integrity_check` and a rebuild of the file. Thanks to @sgreadly (#2960).
