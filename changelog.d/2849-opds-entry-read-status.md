### Added

- **OPDS entries now say whether you have read the book, and carry its book id.** Each book in an OPDS feed has a `<calibre:id>` with its numeric id, and for a signed-in account a `<cwng:read_status>` (`finished`, `in_progress`, `unread`, `did_not_finish` or `on_hold`), so a reading app can show a read mark in any list without fetching the read-list feeds one by one. Guests get the id only. Apps that do not know these elements ignore them (#2849).
