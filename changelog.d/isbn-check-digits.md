### Fixed

- **Automatic metadata fetch checks ISBN check digits.** A book's ISBN-10 now finds the provider record carrying the same edition's ISBN-13, an ISBN that fails its check digit no longer picks a record, and a provider's invalid ISBN is no longer written to the book.
