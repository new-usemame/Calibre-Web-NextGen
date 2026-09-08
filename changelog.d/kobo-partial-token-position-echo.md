### Fixed

- **Kobo reading-position confirmations interrupted by a partial or store sync
  token are now retried.** Books that remain entitled are retried, while an
  entitlement removal cancels the device's pending repair state.
