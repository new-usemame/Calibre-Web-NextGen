### Fixed

- **Data left behind by users deleted before v4.1.45 is now cleaned up.** Before v4.1.45, deleting a user left the account's e-readers and browsers (names, identities, storage snapshots), KOReader reading positions and magic shelves in the server's database. Since v4.1.45, deleting a user removes them, and now the server also clears what earlier deletions left behind each time it starts. Other accounts' data is not touched (#2563).
