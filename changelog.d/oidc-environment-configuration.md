### Added

- **Generic OIDC can be deployed without writing provider secrets to the database.** Configure the `GENERIC_OAUTH_*` environment variables (or mount `GENERIC_OAUTH_CLIENT_SECRET_FILE`) to activate the provider, set endpoint and group/default-role policy, and keep the admin page read-only. Restart after rotating a mounted client-secret file.
