### Fixed

- **A book not yet downloaded to a KOReader device carries its Calibre tags.** The small stand-in EPUB a device gets for a cloud book listed the title, authors, language and series but no tags, so KOReader's Book information showed no Keywords and anything grouping books by tag on the device saw every cloud book as untagged. Each tag is now a `dc:subject` in the stand-in, and a tag change (including a rename that leaves the book's modified time alone) makes devices fetch the stand-in again on their next sync. Reported by @adayag (#2792).
