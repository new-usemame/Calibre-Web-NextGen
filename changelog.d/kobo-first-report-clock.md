### Fixed

- **A Kobo's first report for a book is kept whole.** Opening a book on a Kobo for the first time no longer loses its read status or its clock: the server's empty reading-state rows used to be stamped with the server's own time, which counted as newer than the Kobo's report, so a "Finished" from that first sync was dropped, the Kobo was sent the server's clock instead of its own, and a second device's later update could be rejected. Empty rows now count as holding nothing until a device reports.
