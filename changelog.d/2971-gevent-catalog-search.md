### Added

- **Large catalog searches can run in a background worker.** An opt-in gevent worker lets other API requests continue while SQLite finds matching books.
