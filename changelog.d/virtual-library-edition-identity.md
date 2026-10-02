### Fixed

- **Requesting a book could silently retain a different edition with the same title and author.** Book sources now compare prepared file bytes before reusing an existing library record. Different files import separately, preserving the old book, annotations and reading position. New imports and in-progress recovery reinspect older metadata-only retention results; historical completed receipts stay unchanged.
