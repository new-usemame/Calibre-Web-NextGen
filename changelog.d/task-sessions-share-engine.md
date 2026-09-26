### Fixed

- **Long-running servers no longer creep towards "too many open files".**
  Each run of a background task (thumbnails, temp-folder cleanup, KEPUB
  repair, annotation backup and sync, conversions) opened a fresh connection
  pool to the app database, and many of those connections stayed open until the
  server restarted. The tasks now share one pool and hand their connection back
  when they finish.
