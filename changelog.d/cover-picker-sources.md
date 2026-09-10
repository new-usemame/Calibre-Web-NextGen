### Fixed

- **The cover picker now asks every source for the book's title and author, and tells you when a source refused instead of "No results".**
  It used to search all sources with the book's ISBN alone, which most catalogues and shops cannot resolve for an
  edition they do not stock; on one household library that turned a well-known novel into "1 of 15 sources
  answered". A source that rejects the configured key (Hardcover), runs out of shared quota (Google Books,
  ComicVine) or blocks the request (Amazon) is now labelled as such, with the remedy, in both the cover picker
  and the metadata search. You can also re-run the sources with your own words from the picker's toolbar, and
  the server log carries one line per search naming each source's outcome. Kobo searches with non-ASCII titles
  or authors no longer fail with HTTP 400.
