### Fixed

- **Installs outside Docker no longer fall back to the container's folders.**
  Library auto-detect and the admin database page look for `metadata.db` in the
  configured library folder, the duplicate-merge disk-space check measures the
  real config folder, `generate_book_checksums.py` defaults to the configured
  library, and Calibre user plugins load from the config folder named by
  `CALIBRE_DBPATH`. The sanitized debug pack now hides this install's own config
  and library paths, which off Docker often include a username. In network-share
  mode, a relocated ingest, library or config share is no longer chowned by
  uploads or the updater. Contributed by @BeatLink (#2402).
