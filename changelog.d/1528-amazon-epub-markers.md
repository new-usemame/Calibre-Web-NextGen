### Fixed

- **Kindle EPUB Fixer removes Amazon conversion markers without deleting book content.** The invalid `data-AmznRemoved` attribute is removed from parsed markup while its element, text, formatting and other attributes stay intact. XML charset tags and BOM declarations are preserved during the same fixer run. Thanks to @sltvtr for reporting #1528.

- **Single-book EPUB repairs enforce administrator and CSRF permissions.** The manual repair endpoint now uses the same administrator boundary as the EPUB Fixer service and accepts the token sent by its existing page.

- **Manual EPUB Fixer runs save their repair history successfully.** Single-book runs no longer stop with a SQLite path error after rewriting the book, and repeat runs record that no repairs were needed.

- **Go to note keeps the revealed footnote text on screen with enlarged reader text.** The web reader expands a publisher-hidden note before measuring its destination, so the later pagination update does not return the reader to an earlier page. This adjacent #2255 regression was found while verifying the Kindle repair integration.
