### Added

- **Choose a private cover without changing the shared library.** Each user can
  upload a cover, paste a cover URL, or choose one from the cover picker. The
  personal cover appears only in that user's views and e-reader deliveries;
  administrators still control the library cover seen by everyone else.

### Fixed

- **Cover writes are now crash-safe.** Global covers publish only after their
  metadata transaction succeeds. Personal covers publish under an immutable
  version name before the preference transaction points at them, so a failed
  database commit cannot make a committed preference serve different bytes.
- **Personal covers no longer re-download held Kobo books.** Changing the image
  leaves the entitlement fingerprint and device ledger alone, while the
  authenticated cover endpoint and EPUB/KEPUB delivery use the current user's
  image without exposing it to another account.
