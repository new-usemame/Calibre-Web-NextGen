### Fixed

- **Footnote markers and other in-book links now stay in the book.** On iPhone
  and iPad Safari, tapping a footnote marker in the browser reader replaced the
  book with the library home page inside the reader. Footnote and endnote
  markers — in either the EPUB 3 (`epub:type="noteref"`) or the DPUB-ARIA
  (`role="doc-noteref"`) vocabulary, and whether the note lives in the same
  document or another one — now open the note in a dismissible panel with a
  "Go to note" action, other in-book links move the reader to their target, and
  external links open in a new tab. Link targets are at least 24 px, so a
  footnote marker is reachable with a thumb. A reader window can no longer be
  handed a page of the app: a format the reader cannot open answers 404 instead
  of redirecting to the library.
