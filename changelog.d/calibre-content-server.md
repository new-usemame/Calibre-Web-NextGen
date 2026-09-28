### Added

- **An embedded calibre content server can run alongside the web UI.** Off by
  default; when enabled, calibre's own content server starts as a managed
  subprocess with either basic-auth credentials or an explicit anonymous-writes
  mode, plus a configurable listen address and trusted IPs. calibredb operations
  route through the running server so its in-memory copy of the library stays in
  step, and fall back to the library path whenever the server is stopped or not
  answering. The server reloads when the library database changes externally,
  and is restarted if it exits on its own. The configured password is never
  passed as a command-line argument, and the user database calibre keeps it in
  is owner-only.
