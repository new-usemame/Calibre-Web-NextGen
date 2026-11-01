### Fixed

- **A book with two saved Kobo places sends the newest one again.** Some databases held two saved places for one book, and the server then read one of them at random, so a place saved by another device could be held back from the Kobo. On start the server now keeps the place it received last, removes the extra copy and any saved place left without a book, and refuses a second copy from then on. Reported by @lukerajah (#2783).
