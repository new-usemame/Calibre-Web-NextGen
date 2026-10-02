### Added

- **NixOS can run NextGen without Docker.** The repository is now a Nix flake with a package and a NixOS module (`services.calibre-web-nextgen`) that runs the web app, the ingest watcher and the other background services as systemd units, creating `app.db` and an empty library on first start like the container does. See the README's NixOS section.
