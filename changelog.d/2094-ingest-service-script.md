### Changed

- **The ingest service can run outside the container.** Its body moved from the s6 `run` file to `scripts/services/cwa-ingest-service.sh`, and the paths it used to hardcode under `/config` (`cwa.db`, the retry queue, status and batch files, the failed-books folder) follow `CALIBRE_DBPATH`. The container sets that to `/config` for this service, as it already does for the web app, so nothing moves there. Part of #2094.
