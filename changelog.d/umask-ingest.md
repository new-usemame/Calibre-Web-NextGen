### Fixed
- **`UMASK` now applies to ingest, the metadata enforcer, the auto-zipper and the other background services, not only the web app.** Books imported with `UMASK=027` got 755 folders, because the base image applies `UMASK` only to services named `svc-*`. An invalid value is now named in the service log instead of being ignored silently. Thanks @sgreadly (#2670).
