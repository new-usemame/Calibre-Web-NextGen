### Fixed

- **Shelf counts in the new UI's sidebar stay in step with the shelf.** Archiving, hiding or merging a book left its shelf's count at the old number until the page was reloaded; the count now updates straight away. On v4.1.43 a shelf could also count books that had been deleted outside the app (for example in Calibre desktop), so the badge sat one or more above what the shelf showed until the shelf was opened in the classic UI; the count now includes only books that still exist. Reported by @theSeanO (#2235).
