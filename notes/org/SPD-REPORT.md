# SPD feature-request wave

- #498: implemented a per-user saved advanced-search default using `User.view_settings`; root renders the saved filter, reload preserves it, and Clear returns to Catalog. Unit tests and production frontend build pass. Live `cwn-spd` desktop save → root → reload → clear is OBSERVED. At 360px Luna OBSERVED accessible controls and `scrollWidth == clientWidth`; its short-lived runs ended before the complete mobile persistence chain/report, so that link remains ASSUMED pending a later rerun. No new route.
- #878: implemented canonical API timestamps, Book Detail metadata rows, and sortable Date added / Last modified Table columns. Red/green serializer coverage (28 related tests green) and production frontend build pass; sortable headers follow the existing keyboard/`aria-sort` pattern. Live visual rerun pending after branch image rebuild.
- #783: pending.
- #330 / #228 / #548 design dispositions: pending.
