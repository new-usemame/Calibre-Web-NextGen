### Added

- **Personal Library accounts can remove several books from their own library at
  once.** Selecting books previously offered only *Delete*, which erases them
  from the global library for every user on the server — one click away from a
  person whose intent was simply to tidy their own shelf. *Remove from my
  library* is now the primary bulk action in Personal Library mode, and the two
  are named for their scope rather than distinguished by colour alone: removal
  takes the books out of your library, your OPDS feed and any regular shelves
  you put them on, keeps your highlights, notes, bookmarks and reading progress,
  and deletes nothing from the global library.

### Fixed

- **A refused bulk removal now says why.** Accounts that cannot browse the
  global library are not allowed to empty their library completely, so
  selecting everything left one book behind and reported only that it "failed".
  The reason is now shown, once, however many books it applies to — and an
  oversized batch reports its limit instead of failing silently.
