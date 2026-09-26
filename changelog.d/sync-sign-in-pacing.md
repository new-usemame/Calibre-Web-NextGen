### Fixed

- **KOReader sync slows down repeated wrong passwords, without ever slowing you down.**
  Sign-ins to KOReader progress, annotation and library sync now pace wrong
  passwords the way the rest of the app does. The pacing is kept per device
  address, so a Kindle still using a revoked app password slows only itself,
  and your other devices keep signing in straight away. OPDS catalogue sign-ins
  are paced the same way now, so one misconfigured reader app can no longer
  keep your account's other OPDS apps waiting.
