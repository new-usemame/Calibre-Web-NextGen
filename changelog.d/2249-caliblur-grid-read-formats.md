### Fixed

- **The read icon on a caliBlur book cover no longer opens a reader that cannot show the book.** On a book whose only format is MOBI or AZW3, it redirected to the library with "Selected book is unavailable. File does not exist or is not accessible", even though the file was fine. It now opens the book's detail page instead, and for readable books it opens the same format as the detail page's "Read now", with EPUB preferred. Reported and first fixed by @splitsec2 (#2249, #2250).
