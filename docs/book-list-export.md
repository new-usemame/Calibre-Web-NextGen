# Export a book list

Choose **Export CSV** or **Export TXT** on a supported library, search, shelf or smart-shelf list. The file contains all books matching the list’s filters, including pages you have not loaded. Sign in with a personal account to export. Your account’s book visibility still applies, including the existing category context on Classic tag pages. Global Library export requires the same access as browsing that archive.

CSV contains title, authors, series and index, tags, rating, read status, formats and date added. Open it in Excel, LibreOffice, Numbers or a text-processing tool. Native Excel files are not generated. Formula-like text is marked as text so a book title cannot become a spreadsheet formula.

TXT contains one book per line with its title and authors. Line breaks inside those fields are flattened.

The maximum is 100,000 matching books. A larger list reports an error and asks you to narrow its filters; no partial export is silently produced. A failed download can be retried without changing your filters or books. SQLite builds without JSON1 report an explicit error for large saved ID lists rather than exceeding their safe parameter budget.

Random Discover picks and Hot are separate feeds and currently have no full-list export action. Classic custom-column and “None” category browse pages are also excluded until their precise query can be reused; use Advanced Search with the same criteria for an export. Classic Advanced Search exports the criteria captured when that results page rendered, so another tab’s search cannot replace them. Its signed page snapshot expires after 24 hours; reload the results before retrying an expired export. Exporting does not alter read status, shelves or metadata.
