### Fixed

- **The Convert Library and EPUB Fixer pages no longer error before their first run.** On an install where either service had never been started, every status poll returned a server error and wrote a traceback to the log. The pages now show that nothing has run yet. A failed poll no longer stops the page updating: it backs off and retries. The status poll also reads only the end of the run log instead of the whole file, as the cover and metadata enforcement page already did. Reported and first fixed by @TheFactor1 (#2227).

### Security

- **Book titles in a Convert Library or EPUB Fixer run log are shown as text.** Both pages inserted the live log into the page as HTML. The log includes book titles and filenames, so a title containing markup could run script in the admin's session. They now render the log as plain text, as the enforcement page already did.
