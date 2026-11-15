### Fixed

- **Windows startup and library maintenance no longer fail on Unix-only APIs.** Kobo startup tolerates the absence of fork hooks, ingest uses the shared cross-platform process lock, and service scripts skip Unix account and ownership operations when unavailable. Library refresh, conversion, EPUB fixing, and cover enforcement use the running Python interpreter, including virtual environments. Thanks to @Rol3333. (#2614)
