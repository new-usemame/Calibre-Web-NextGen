### Fixed

- **The Library no longer gets stuck on "Loading…" at certain window widths with Rows per load set to 1.** The number of books fetched per page follows the number of columns, and a scrollbar appearing as rows loaded could cost a column, which threw the loaded list away and started it again, repeatedly. The page size now holds once you are past the first page; changing Rows per load or density still starts the list afresh (#2903).
