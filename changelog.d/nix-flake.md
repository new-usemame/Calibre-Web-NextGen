### Added

- **NixOS can run NextGen without Docker.** The repository is now a Nix flake with a package and a NixOS module (`services.calibre-web-nextgen`) that runs the web app as a systemd service, creating `app.db` and an empty library on first start like the container does. The ingest watcher and other background services are not included yet, so new books are not imported on NixOS until they are. See the README's NixOS section.
