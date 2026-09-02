### Fixed

- **Book deletion now reports what actually happened.** Bulk actions count and
  list failed books, leave only those books selected for an immediate retry,
  and distinguish a completed database deletion with incomplete file cleanup
  from a clean success.
