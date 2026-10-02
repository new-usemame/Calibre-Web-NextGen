### Added

- **Calibre clients can use an optional managed content server alongside the web UI.**
  Disabled by default, with separate authenticated credentials or an explicit
  anonymous mode. NextGen routes supported Calibre operations through the
  service, pauses it while conversion/restore owns the library, reloads after
  quiet database changes, and stops repeated startup failures. Split libraries
  are refused for now. Based on @benjitobz’s contribution in #2210, with
  maintainer fixes for authentication, process ownership and lifecycle handling.
