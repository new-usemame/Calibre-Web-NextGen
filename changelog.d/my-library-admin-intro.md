### Added

- **A "Try My Library" card now welcomes admins on User administration.** One
  click switches every non-Guest account to My Library (each account keeps a
  seeded copy of everything it can see and gains global-library browsing), with
  the previous roles and modes snapshotted first — the **Undo** button is a
  true restore, and each account's selection lies dormant, ready to come back
  exactly as it was. Once enabled, the card can be closed permanently; before
  that it has no close control. The card's state is stored on the server, so it
  is shared by all administrators and survives sessions.

### Changed

- **The library-mode pair is now named "The global library" vs "My Library"**
  everywhere it appears — the admin user editor, the Account page, the classic
  user pages, confirmation dialogs, and the intro banners — matching the menu
  names the app already uses. French and Dutch translations are complete for
  all new and renamed strings.
- **The Library contents section of User administration is redesigned.** The
  two modes are now selectable cards (the same checked-tint idiom as the
  Account page), and the longer explanation of how switching works sits one tap
  behind an info toggle instead of always occupying the card.
- The "Set up My Library for all users" header button is removed; the intro
  card's Try/Undo flow is the one place that bulk action lives.
