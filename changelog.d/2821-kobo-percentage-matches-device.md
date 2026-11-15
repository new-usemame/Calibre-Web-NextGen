### Fixed

- **A Kobo library shows a percentage calculated for the exact page shared from the web reader or KOReader.** The web reader counts the book by text and a Kobo by the size of each KEPUB document, so the same place was 26% in one and 16% in the other, and the shelf showed the web's figure until the book was opened and closed. The exact place is now sent with a byte-weighted percentage, including when a sync reply is retried. Thanks to @sgreadly for the fix and device measurements. (#2821, #2825)
