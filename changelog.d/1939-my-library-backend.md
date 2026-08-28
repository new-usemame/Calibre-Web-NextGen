### Added

- **Each account can now keep its own selection of books, out of one shared library.** Useful on a
  server holding a big archive where one person only reads a few dozen of them. There is still one copy
  of each file on disk and one set of metadata — only *which books you keep* is per-account, so two
  readers can both have a book without there being two copies of it.

  **Upgrading changes nothing.** Every existing and new account stays in *the whole library* mode, which
  is exactly how the server behaved before. Nothing switches on by itself and no book moves.

  When you do switch an account to *my selection*, it starts out holding everything that account can
  already see, so the change is invisible on day one — including on an e-reader, which keeps every book
  it already had. Pruning afterwards is a book-at-a-time decision. Switching back and forth loses
  nothing: your selection is restored exactly, even if you had deliberately emptied it.

  **Removing a book from your library deletes nothing** — not the file, not the metadata, not your
  highlights, notes or reading position. Add it back later and your notes and your place in the book are
  where you left them. It does drop the book from your own shelves, and re-adding does not put it back
  on them. If you have an e-reader, the book leaves the device on its next update, and the confirmation
  says so before you commit. Deleting a book *from the global library* is a separate, clearly separated
  action that still erases it for everyone.

  Accounts allowed to browse the whole archive get a **Global Library** section — everything on the
  server, with a *recently added that you don't have* view — and can switch their own mode; for other
  accounts an administrator manages it. Administrators can move one account or every account at once
  (safe to re-run, never re-seeds), and can put a specific book into a managed account's selection.
  Shelves, search, facet counts, OPDS and Kobo sync all follow the account's selection. Adding a book to
  a shelf, or uploading one, adds it to your library first — you cannot shelve or upload into a library
  you cannot see. Both the new and classic interfaces have the whole feature.

  ⚠️ **My Library is a curation tool, not a privacy boundary.** It decides what an account sees by
  default, not what it is permitted to reach. To actually keep books away from an account, use the
  existing allowed/denied tags or the restricted custom column, which are enforced separately and still
  apply.

  Two notes for people running a proxy or a bare-metal install: responses whose contents depend on who
  asked now send `Cache-Control: private, no-store` and identity-aware `Vary` headers, so a reverse proxy
  must not reuse cached OPDS or library responses across accounts; and a SQLite build without JSON
  support uses a slower but correct fallback rather than failing. The classic library's saved
  newest-sort key is renamed internally, which resets an existing saved *newest* selection once.

  Implements [#1939](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1939).
