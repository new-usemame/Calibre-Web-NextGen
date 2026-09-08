### Fixed
- **Polling ingest:** New or replaced books are discovered even when Docker Desktop or a network mount caches directory timestamps. A bounded reconciliation scan prevents files from waiting indefinitely while keeping the lightweight polling optimization between scans.
