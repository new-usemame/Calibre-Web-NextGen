### Fixed

- **With two or more Kobos, one Kobo fetching a book through a link from before the update no longer stops the other Kobo picking up the shared place.** CWNG has to guess which Kobo made such a download, and that guess cleared the record that the other Kobo held the current copy of the book, so it went back to its own old page until it downloaded the book again. A guessed download now leaves that record alone. Found by @teisho while testing #2724.
