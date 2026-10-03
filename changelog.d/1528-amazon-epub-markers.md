### Fixed

- **Kindle EPUB Fixer removes Amazon conversion markers without deleting book content.** The invalid `data-AmznRemoved` attribute is removed from parsed markup while its element, text, formatting and other attributes stay intact. XML charset tags and BOM declarations are preserved during the same fixer run. Thanks to @sltvtr for reporting #1528.
