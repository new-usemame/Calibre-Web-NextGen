### Fixed
- **`UMASK` now applies to the ingest service and every other library-writing service.** Services not named `svc-*` previously ignored it and created 755 folders (#2670).
