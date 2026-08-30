### Fixed

- **Kobo library sync no longer closes the shared library database connection
  underneath other requests.** Sync still refreshes its view of books written
  by Calibre desktop or a network-share workflow, but now uses the existing
  non-disposing refresh path. If that refresh cannot complete, the request
  returns a defined service-unavailable response and writes a Kobo-specific
  error to the server log instead of disappearing mid-sync (#1977, #1857).
