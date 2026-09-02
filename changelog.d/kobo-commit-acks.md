### Fixed

- **Kobo collection changes are retried when the database is temporarily busy.**
  Creating, renaming, deleting, or changing the books in a collection no longer
  tells the device that the change succeeded after its database write was
  rolled back. Book removals use the same fail-closed acknowledgement rule.
