### Fixed

- **The Convert Library and EPUB Fixer pages no longer error before their
  first run.** Each service writes its log only when it first runs, so on a
  fresh install the file does not exist yet — and the status both pages poll
  read it without checking, returning a 500 and writing a
  `FileNotFoundError` traceback to the container log for as long as the page
  stayed open. A service that has never run now simply reports no progress. A
  log that exists but cannot be read (after a `PUID`/`PGID` change, say)
  degrades the same way instead of failing the request.
