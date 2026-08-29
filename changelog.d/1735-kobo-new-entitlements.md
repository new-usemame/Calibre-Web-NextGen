### Fixed

- **Large Kobo libraries no longer stop adding books after the first sync
  page.** New-versus-changed entitlement classification now follows each
  physical Kobo's delivery record instead of comparing unrelated library
  timestamps. Devices already affected recover their missing books as new
  entitlements on their next sync, without a factory reset or token reset;
  confirmed earlier deliveries remain changes rather than being announced as
  new again (#1735).
