### Fixed

- **KOReader sync and OPDS sign-ins now slow down password guessing without
  locking out your devices.** A device or app that keeps sending *different*
  wrong passwords for an account is refused for a minute after three. A device
  stuck on an old or revoked password is sending the same wrong password each
  time, so it gets "wrong password", never a lockout. Your other devices, and a
  right password on the same home network, keep signing in straight away.
  Devices that sign in with an app password are never slowed. Before, KOReader
  sync did not slow wrong passwords at all. OPDS paced every sign-in to an
  account together, so one misconfigured reader app could keep the account's
  other OPDS apps waiting.
