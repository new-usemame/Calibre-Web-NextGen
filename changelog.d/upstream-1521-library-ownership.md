### Fixed

- **Ingest and library conversion avoid redundant ownership writes on large libraries.** Only entries with a different owner or group are handed to chown. Adapted from [CWA #1521](https://github.com/crocodilestick/Calibre-Web-Automated/pull/1521) by @manut999.
