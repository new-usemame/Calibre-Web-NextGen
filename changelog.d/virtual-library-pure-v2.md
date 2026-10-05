### Added

- **Pure-v2 torrent files can import books through qBittorrent with libtorrent 2.0.** Original metainfo and engine-selected IDs are preserved after bounded BEP52 path and piece-layer validation. Unsupported clients refuse before submission; qBittorrent requests can be retried after compatibility is restored. V1/hybrid files and v1 magnets keep their existing behavior. Pure-v2 magnets remain unsupported.
