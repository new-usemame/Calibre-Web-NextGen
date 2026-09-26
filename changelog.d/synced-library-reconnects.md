### Fixed

- **Books copied onto the server by a sync tool now appear without "Reconnect
  Calibre Database".** Tools such as rsync, Syncthing and NAS sync apps replace
  `metadata.db` with a new file, and the server kept reading the old one until
  someone reconnected by hand. The server now notices the replaced file within
  a few seconds and switches to it on its own (#2291, thanks @Dirk71).
