### Fixed

- **Import supported v1 torrents with reviewed file metadata.** Valid EPUB/PDF torrents containing optional per-file SHA1 hints or single-file hidden/executable flags no longer fail before submission. Original torrent hashes are preserved; malformed piece totals, private flags and padding-only payloads are refused.
