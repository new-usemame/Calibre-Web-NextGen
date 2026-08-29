### Fixed

- **Replacing a cover from the new interface works on your own hidden or
  archived books.** The edit page opened for them, but saving a new cover
  answered "Book not found" — the cover endpoint resolved the book more
  strictly than the page that linked to it.
- **A locked cover can no longer be replaced from the new interface.** Locking
  a cover already stopped the cover picker, the classic editor and the
  automatic metadata fetch from touching it; the new interface's edit page
  overwrote it anyway. It now refuses, the same way the picker does.
