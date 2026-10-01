### Added

- **Retained originals can live on another disk.** Set `CWA_PROCESSED_BOOKS_DIR` to move the `processed_books` tree (imported and converted originals, EPUB fixer originals, failed imports and duplicate resolutions) out of the config dir. Unset, it stays at `processed_books` in the config dir as before. Requested in #1883.
