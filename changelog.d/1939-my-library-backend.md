### Added

- **Each account can now choose Monolibrary or Personal Library mode over one
  shared Calibre archive.** Monolibrary continuously follows the global
  archive. The first switch to Personal Library safely seeds everything the
  account can already see; later switches restore the exact saved selection,
  including an intentionally empty one. Users can switch their own mode and
  administrators can switch any account. Authorized users can browse the
  global archive and add or remove books from their set; ordinary shelves and
  Kobo sync follow that set, while reading progress, bookmarks, annotations,
  sync settings, roles, and ownership records survive mode changes and later
  re-adds. The new introductory card has a durable per-account dismissal.
  Existing accounts start in Monolibrary mode and remain unchanged. Implements
  [#1939](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1939).
