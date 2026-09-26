### Fixed

- **An e-reader moved to another account now registers there.** A KOReader
  device or Kobo that was used with one account and then paired to another on
  the same server was refused as a device for the new account, so Send to
  device, its device page, the book list it reports and removing books from it
  all failed. Each account now gets its own entry for the reader. Switching
  back finds the first account's entry again with its name and history, and
  neither account can see or change the other's.
