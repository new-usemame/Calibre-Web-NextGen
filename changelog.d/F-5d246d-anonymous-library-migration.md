### Fixed

- **“Set up My Library for all users” leaves the public Guest account unchanged.**
  The bulk setup action now migrates only non-anonymous accounts, reports that
  Guest was skipped, and keeps the per-user control available when an
  administrator deliberately wants a curated public library.
