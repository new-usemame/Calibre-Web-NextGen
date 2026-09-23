### Fixed

- **The Convert Library and EPUB Fixer pages no longer error before their first run.** On an install where either service had never been started, every status poll returned a server error and wrote a traceback to the log. The pages now show that nothing has run yet. If a poll fails, the page says it lost contact and retries instead of silently freezing. The status poll also reads only the end of the run log instead of the whole file, as the cover and metadata enforcement page already did. Reported and first fixed by @TheFactor1 (#2227).

### Security

- **Text from inside a book is shown as text on the Convert Library and EPUB Fixer pages.** Both pages inserted the live run log into the page as HTML. The log echoes text from inside the library's books, such as EPUB metadata and converter output, so a crafted book could run script in the admin's session while a run was on screen. The pages now render the log as plain text, and the two status endpoints return JSON rather than HTML.
