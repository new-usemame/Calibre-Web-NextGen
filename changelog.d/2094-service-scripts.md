### Changed

- **Five background services can run outside the container.** The auto-zipper, cover-preview cache sweeper, metadata change detector, KOReader checksum backfill and auto-library bodies moved from their s6 `run` files to `scripts/services/*.sh`, which systemd or a plain shell can start. The container runs them exactly as before. The auto-zipper's error messages for exit codes 2 and 3 now print; they used to re-read `$?` after the first check and never matched. Part of #2094.
