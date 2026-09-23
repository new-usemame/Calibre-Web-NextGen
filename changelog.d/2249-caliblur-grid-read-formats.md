### Fixed

- **The read icon on a caliBlur book cover no longer opens a reader that cannot show the book.** On a book whose only format is MOBI or AZW3, it opened a new tab with a "Not Found" error, even though the file was fine. Those covers no longer show the read icon, and clicking the middle of the cover opens the book's details like the rest of the cover does. On readable books the icon opens the same format as the detail page's "Read now", and audiobooks still open the player. Reported and first fixed by @splitsec2 (#2249, #2250).

### Changed

- **"Read now" on a book's detail page opens the EPUB when the book has one.** For a book with both a PDF and an EPUB, it used to open whichever format was added to the library first. It now opens the EPUB, which is also what the caliBlur read icon opens.
