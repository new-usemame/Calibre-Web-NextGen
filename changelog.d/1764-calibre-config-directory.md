### Fixed

- **EPUB repair and conversion no longer require manually changing ownership
  under `/root/.config/calibre` in Docker.** Every s6 service that can launch a
  Calibre tool now supplies a writable config directory for the uid that runs
  it. Normal `abc` work uses a plugin-free directory prepared during container
  initialization, root-run maintenance uses a private temporary directory, and
  the existing user-plugin directory remains active only when its explicit
  opt-in is enabled.
