### Fixed

- **Concurrent My Library removals can no longer empty an administrator-managed
  account.** The last-book rule is now enforced by the same database statement
  that removes membership, including when app.db uses rollback journaling on a
  network share. Batch removal results also disclose the next-sync Kobo removal
  and preservation of reading data, matching the one-book action.
