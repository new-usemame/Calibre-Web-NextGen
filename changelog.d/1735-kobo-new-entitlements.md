### Fixed

- **Large Kobo libraries no longer stop adding books after the first sync
  page.** New-versus-changed entitlement classification now follows each
  physical Kobo's delivery record instead of comparing unrelated library
  timestamps, and confirmed earlier deliveries remain changes rather than
  being announced as new again. A reader already missing books this way gets
  them on its next sync as new entitlements, without a factory reset or token
  reset. The exception is a book the same account downloaded some other way,
  in a browser or on another Kobo: for that book use **Resend one book to
  this Kobo** or **Force full kobo sync** (#1735).
