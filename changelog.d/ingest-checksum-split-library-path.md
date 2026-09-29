### Fixed

- **Reading-progress checksums are generated in the right database again on a split library.** After importing a book, with KOReader sync enabled, checksum generation looked for `metadata.db` in your book-storage folder instead of where it actually lives, so it always failed with "no such table: books" and left a stray, empty `metadata.db` file sitting in that folder on every import (harmless, but it kept happening). Plain (non-split) libraries were never affected. Thanks to @sgreadly for the report.
