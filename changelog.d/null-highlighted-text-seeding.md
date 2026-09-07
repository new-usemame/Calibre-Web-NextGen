### Fixed

- **Bookmarks and highlights saved before Calibre-Web recorded their text no longer stop a book from finishing its sync to your Kobo.** Those older entries have no saved text, and Calibre-Web was reading that missing text as a disagreement with your Kobo rather than as something it simply did not have yet — so the book was held back for safety and could never finish. Calibre-Web now takes the text from your Kobo, which is where it was recorded, and the book syncs. If the two genuinely hold different text, the book is still held back and your copy is left untouched.
