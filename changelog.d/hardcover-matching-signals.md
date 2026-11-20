### Fixed

- **Reading progress can reach Hardcover when a matched book has no edition
  selected.** Sync chooses an edition with pages, preferring the book's own
  identifier, Hardcover's defaults, then popular e-books. Works with more
  than 100 editions. Thanks @lguerard (#2503).

### Added

- **Admins can browse books without a Hardcover ID by review status, allow
  rejected books to be tried again, and re-run matching over pending reviews.**
  The list is paginated and translated into French and Dutch. Thanks @lguerard (#2503).

### Changed

- **Hardcover auto-fetch uses ISBNs, alternative titles, subtitles and accents
  to find translated and subtitled books, and breaks equal scores by popularity.**
  Invalid ISBNs and conflicting subtitles cannot bypass those comparisons.
  Thanks @lguerard (#2503).
