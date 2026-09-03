### Fixed

- **Simultaneous reading-position syncs now preserve the furthest real
  bookmark.** KOReader, Kobo, and browser writers now arbitrate each position
  update inside the database, so a delayed lower percentage cannot replace
  newer progress or discard a KOReader locator.
