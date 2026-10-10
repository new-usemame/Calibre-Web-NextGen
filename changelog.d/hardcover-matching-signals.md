### Fixed

- **The Hardcover review buttons work again in French and Italian.** A
  translated message containing an apostrophe ("Échec de l'application…")
  broke the page's script, so "Select This Match", "Reject" and "Skip" did
  nothing and sent no request.
- **Reading progress reaches Hardcover for books matched without an
  edition.** Books linked by the auto-fetch, the review page or a shelf
  sync have no edition, hence no page count, so every push stopped at
  "Hardcover user_book has no edition page count". The sync now picks an
  edition with pages (the book's `hardcover-edition`, then Hardcover's
  default e-book or physical edition, then the most shelved e-book), uses
  it, and saves it on Hardcover.

### Added

- **Admins can list every book that has no Hardcover ID, and see why.**
  Reading progress, read status and annotations never reach Hardcover for
  those books, and nothing said so. The new page (Admin, next to "Review
  Hardcover Matches") shows each one as waiting for review, rejected,
  skipped or not matched yet, links to its edit page, lets a rejected book
  be searched again, and can re-run matching over all of them, books
  waiting for review included.

### Changed

- **Hardcover auto-fetch matches translated books, subtitled titles and
  books with an ISBN.** It now searches by ISBN first (Hardcover files
  every edition's ISBN under the book, so a French or German edition finds
  the original work), compares the title against Hardcover's alternative
  titles and with or without a subtitle, ignores accents, and prefers the
  most shelved record when Hardcover holds duplicates. Books such as "Les
  filles de la villa aux étoffes" or "Play Nice" used to land in the review
  queue.
