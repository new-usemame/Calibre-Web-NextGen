### Added

- **Highlights, notes and bookmarks from word-based reading apps now sync with KOReader, the web reader and Kobo.** A KOSync client that keeps no KOReader or epub.js locator can send a highlight as its words plus the few words around them. The server finds the passage in the library EPUB, so KOReader on the same file and the web reader show it on the same words. Highlights made in KOReader, the web reader or on a Kobo come back to that client as words. A passage that cannot be found is kept, not guessed or dropped. Each app can delete only its own highlights. Words are exchanged only for accounts that may read or download the book. A Calibre book id works as the document key on these routes too.

### Fixed

- **An edit to a highlight from a KOReader device now advances its revision.** Before this, an edit pushed through the KOReader plugin left the highlight's revision unchanged, so another device comparing revisions could miss the edit.
