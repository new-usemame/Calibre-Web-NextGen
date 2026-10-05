### Changed

- **Keep writable cache with persistent configuration.** The general cache
  defaults to `/config/cache` in Docker instead of the application tree.
  Explicit `CACHE_DIR` settings and existing thumbnail storage stay unchanged;
  disposable general cache entries regenerate without deleting the old cache.
  Completes the remaining cache part of #995.
