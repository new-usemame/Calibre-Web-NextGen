### Added

- **Force Full Sync now warns that a large Kobo library can take many syncs to catch up.** Both interfaces explain that forcing another full sync during catch-up starts it over. The Kobo sync log also shows `candidates_ahead` for each newly built page. It counts the books selected for the current catch-up, including the current page, books the Kobo already holds and books CWNG needs to check again. Switching between Global Library and My Library can change which books are in scope. (#3003)
