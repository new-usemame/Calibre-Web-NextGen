### Fixed

- **ISBNs are checked by their check digit before they are used.** In automatic metadata fetch, a book's ISBN-10 now finds the provider record carrying the same edition's ISBN-13, an ISBN that fails its check digit no longer picks a record, and a provider's invalid ISBN is no longer written to the book. The cover booster and cover picker no longer look up a cover by an invalid ISBN, Apple Books no longer treats one as an ISBN lookup, and Kobo results drop one.
