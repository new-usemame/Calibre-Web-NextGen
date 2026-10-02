### Fixed

- **Changing the value of an identifier a book already has is saved now.** Correcting an ISBN or a hardcover-id in the book editor used to look successful and show the new value, but reloading the book brought the old one back. Adding and removing identifiers already worked; only changing the value of one that was already there was lost. It now saves. (#2387, reported with the root cause by @garionjb)
