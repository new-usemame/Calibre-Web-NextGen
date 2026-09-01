### Changed

- **The new interface now opens by default for every browser session and login.** Browsers that cannot run it fall back to Classic for the rest of that session, while command-line, OPDS, Kobo, API, and device clients keep their existing non-redirect behavior. Login deep links are carried through the new login screen, and redirecting there no longer leaves Classic-only login or architecture messages queued to appear later on an unrelated page (#1959).
- **Catalog visibility choices now follow your account.** Discover visibility, hidden-book visibility, and the per-card Read/edit row carry across browsers and devices for signed-in users, while guest browsing keeps the existing browser-local settings.
