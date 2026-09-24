### Fixed

- **Large Kobo libraries no longer stop adding books after the first sync
  page.** New-versus-changed entitlement classification now follows each
  physical Kobo's delivery record instead of comparing unrelated library
  timestamps, and confirmed earlier deliveries remain changes rather than
  being announced as new again. A reader upgraded from a release older than
  v4.1.43, or one of several Kobos on its account, gets the books it is
  missing on its next sync as new entitlements, without a factory reset or
  token reset. A reader upgraded from v4.1.43 that is its account's only Kobo
  keeps its delivery record instead, so a book it is missing needs **Resend
  one book to this Kobo** or **Force full kobo sync** (#1735).
