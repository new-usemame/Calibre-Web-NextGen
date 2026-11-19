### Fixed

- **Metadata edits can update a downloaded book in Kobo’s My Books without downloading it again.** When NextGen can verify that only metadata changed since the Kobo last synced the book, the next sync refreshes its library entry in place. Publication-date-only edits in the classic editor also reach the next sync. (#2520, #2868, thanks @Andxre)
