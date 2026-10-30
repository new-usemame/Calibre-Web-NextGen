### Added

- **The support pack now includes a manifest and the system state maintainers usually have to ask for.** `manifest.json` records the pack schema version, CWNG version and generation time, and `state/*.json` adds runtime and container limits, free space and mount type per role (config, library, ingest), database health and row counts for `app.db`, `cwa.db` and the Calibre `metadata.db`, and scheduler and ingest queue depth. Counts, sizes and versions only: no paths, IPs, usernames, titles or row contents (#2728).
