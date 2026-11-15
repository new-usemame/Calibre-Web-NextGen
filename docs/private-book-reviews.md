# Private book reviews

Open a book’s detail page and use **Your review** to add a review or personal note. Sign in to a real account; guest browsing has no private review. Any signed-in reader who can open the book can keep a note, without permission to edit library metadata.

Each account has one plain-text note per book. Whitespace and Unicode are preserved; Save accepts up to 10,000 Unicode characters and Delete asks for confirmation. Failed saves retain the draft for retry. Cancel discards only the current unsaved edit. The New UI and Classic UI share the same saved text. Reviews do not change the shared Description, star rating, reading status or reading progress.

Reviews live in the configuration volume’s app.db and follow its backup lifecycle. A duplicate-book merge preserves both distinct notes for each account, destination first with a blank line between them; identical notes appear once. A merged note may exceed the ordinary write limit so authored text is retained. It remains readable; shorten it before saving a further edit. Deleting a book/account or switching to an unrelated library uses the existing per-user book-data cleanup.

Privacy here means the product only returns a review to its owning account, including administrators using the review API. It is not encryption against administrators with direct database/configuration-volume access. The existing book visibility and content restrictions apply.
