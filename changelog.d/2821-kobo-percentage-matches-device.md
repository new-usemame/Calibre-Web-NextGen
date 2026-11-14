### Fixed

- **A Kobo library shows the same percentage the Kobo will report once the book is opened, for a place read in the web reader.** The web reader counts the book by text and a Kobo by the size of each KEPUB document, so the same place was 26% in one and 16% in the other, and the shelf showed the web's figure until the book was opened and closed. The exact place is now sent with the percentage the Kobo works out for it. (#2821)
