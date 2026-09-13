### Added

- **Your Library now opens on Recent: the books you have been reading, newest first, then everything you have not read in the order it was added.** Recency comes from every reader that reports progress — the web reader, a Kobo's sync, KOReader — so picking a book up on one device moves it to the top on the others. It is the first entry in the sort menu, and it is offered on shelves and in the Global Library too, which keep their own defaults. A sort you have chosen yourself still wins.

### Changed

- **The Library's remembered sort is now saved when you pick one, instead of on every visit.** The old key recorded whatever the page happened to be showing, so it could not tell "I sort by Author" apart from "I have never opened this menu" — which is why a sort you never chose used to be remembered as though you had.

### Fixed

- **Reading recorded before this build knew how to timestamp it no longer disappears from Recent.** Positions saved before `bookmark.updated_at` and `kobo_bookmark.created_at` were added carry no clock; they now rank below reading that does, rather than counting as never having read the book.
- **The two reading-position tables are now indexed by how they are read.** `bookmark` carried no index at all and `kobo_bookmark` none on its parent, so every lookup of a saved position scanned the whole table — including the ones the reader makes each time you open a book.
