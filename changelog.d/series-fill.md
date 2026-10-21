### Added

- **Wikidata as a metadata source for series and series order.** It needs no key. It returns a book's series and position, including positions like 24.5, from Wikidata's catalogue of works.

### Fixed

- **Automatic metadata fetch fills in a missing series from another source.** When the source that matched a book gives no series (Google Books, Open Library and IBDb never do), up to three sources that carry series are asked next, in your provider order, and only the series and position are taken from a result that is the same book.
- **A source's other spelling of a series you already have no longer creates a second series.** "Dune Chronicles" or "Red Rising Saga" join the library's "Dune" and "Red Rising".
- **Series positions such as 24.5 are no longer rounded down** from Kobo and Amazon, and Goodreads books whose page only names the series in the title, as in "Cross Kill (Alex Cross, #23.5)", now get that series.
