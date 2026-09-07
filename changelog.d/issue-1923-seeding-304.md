### Fixed

- **Highlights on a book from your Calibre-Web library are no longer lost when your Kobo re-downloads it before that book has finished syncing to Calibre-Web.** Calibre-Web was passing your reader's cached Kobo tag along when it fetched the book's annotations, so Kobo replied "nothing changed" with an empty answer — and your Kobo, which clears a book's highlights during a download and refills them from that answer, was left with none. The same empty answer also stopped the book from ever finishing its sync, so it stayed exposed to the same loss on every later re-download.
