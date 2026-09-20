### Fixed

- **The read button on a book cover no longer opens a reader that cannot show the book.** In the caliBlur theme, clicking read on a book whose only format is MOBI or AZW3 redirected to the library with "Oops! Selected book is unavailable. File does not exist or is not accessible", which described a missing file rather than an unsupported format. The read action is now offered only for formats the reader renders, and a book with none of them opens its detail page instead.
