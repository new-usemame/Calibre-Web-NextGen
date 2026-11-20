### Added

- **Automatic metadata fetch retries Databáze knih by ISBN.** When a book's
  title does not find it — for example a Czech title stored without
  diacritics — and the book has an ISBN, the fetch searches again by that
  ISBN when Databáze knih is in the automatic provider order. The result
  must still match the book before anything is applied. Contributed by @misko903.
