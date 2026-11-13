### Added

- **You can now clear a book's saved reading position without changing its read status.** The new "Clear reading position" entry in a book's More actions menu removes your place in the web reader, on your Kobo and in KOReader together, so a finished book can be reset for a re-read and stay marked read. A device that still holds its own position can send it back the next time it syncs (#2831).

### Fixed

- **"Mark as unread" now also resets the position KOReader keeps for you.** A KOReader that had reported a place was handed that place straight back after the book was marked unread, because each device's own saved position was never cleared. Marking a book unread now removes those too, for KOReader and Kobo devices (#2831).
