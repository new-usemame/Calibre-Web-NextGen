### Added

- **An embedded calibre content server can run alongside the web UI.** Off by
  default; when enabled, calibre's own content server starts as a managed
  subprocess with either basic-auth credentials or an explicit anonymous-writes
  mode, plus a configurable listen address and trusted IPs. CWA's calibredb
  operations route through the running server so the two never contend for the
  library, and the server reloads when the library database changes externally.
