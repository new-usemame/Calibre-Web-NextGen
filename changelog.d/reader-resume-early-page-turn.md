### Fixed

- **Turning a page while a book opens no longer loses your place from your
  e-reader.** When a book had a KOReader or Kobo position and the New UI reader
  opened it at the start while it worked out where that position was, a page
  turn in those first seconds saved the start of the book as your place, and
  the reader never took you to the synced position again. Those early page
  turns now wait for the jump, which still happens. If you pick a chapter,
  link, highlight or search result in that time, the reader keeps your choice
  and offers the synced position instead.
