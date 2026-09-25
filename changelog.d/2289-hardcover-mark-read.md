### Fixed

- **Marking a book as read now marks it Read on Hardcover.** With Hardcover sync turned on and your own Hardcover API key set, marking a book read from the book page, the new UI, bulk edit, or the KOReader library plugin adds it to your Hardcover library as Read, or moves it to Read if it's already there. Marking a book unread doesn't change anything on Hardcover. Books that only have a `hardcover-id` (no edition) now also turn Read on Hardcover when a Kobo or KOReader finishes them; before, they stayed on "Currently Reading". Thanks to @ashtakom for the report (#2289).
