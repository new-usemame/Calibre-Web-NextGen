### Fixed

- **Reading a book in the new UI reader puts it on Currently Reading.** The new reader saved your position but never changed the book's status, so a book read only there stayed Unread and the Currently Reading shelf stayed empty. Opening a book in it now does what the classic reader always did: the book is marked Currently Reading and one more reading session is counted, a refresh does not count as a new session, and a book you already finished stays finished. Reported by @ubikq (#2750).
