### Fixed

- **Split libraries: importing a book no longer logs "Error generating book checksums: no such table: books" or leaves an empty `metadata.db` in your book folder.** With KOReader sync on and book files stored separately from the library database, the post-import step looked for the database in the book folder, so new books had no KOReader sync checksums until the next container restart filled them in. The empty (0-byte) `metadata.db` that earlier imports left in the book folder is safe to delete. Reported and first fixed by @sgreadly (#2371, #2372).
