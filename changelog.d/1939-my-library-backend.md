### Added

- **Each account can now choose Monolibrary or Personal Library mode over one
  shared Calibre archive.** Monolibrary continuously follows the global
  archive. The first switch to Personal Library safely seeds everything the
  account can already see; later switches restore the exact saved selection,
  including an intentionally empty one. Accounts allowed to view the whole
  archive can switch their own mode; otherwise their mode is administrator-
  managed. Administrators also have an explicit seed-once action for one or
  every account, with a per-account report. Authorized users can browse the
  global archive and add or remove books from their set; ordinary shelves and
  Kobo sync follow that set, while reading progress, bookmarks, annotations,
  sync settings, roles, and ownership records survive mode changes and later
  re-adds. The new introductory card has a durable per-account dismissal.
  Recently added global books absent from a personal set have a dedicated
  discovery filter, and a successful browser upload joins the uploader's own
  library. The React and classic interfaces now expose the same mode controls,
  global-library discovery, membership actions, introduction, confirmations,
  and explanatory empty states. SQLite builds without JSON functions use a
  compatible fallback. KOReader progress exports apply that same canonical
  per-user visibility policy, and a rejected first switch can no longer leave
  the durable seed marker ahead of the user's actual library state.
  Existing and new accounts start in Monolibrary mode; upgrades do not switch
  anyone automatically. Implements
  [#1939](https://github.com/new-usemame/Calibre-Web-NextGen/issues/1939).
