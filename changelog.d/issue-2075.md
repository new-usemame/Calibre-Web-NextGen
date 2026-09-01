### Fixed

- **KOReader devices that only push reading progress now appear on the Devices page.** Progress pushes register a device when they include a stable `device_id`; pushes without one still save progress without inventing an identity. Repeat pushes from a device already on the page no longer write to the database every time, and a KOReader device you renamed keeps the name you gave it. As with Kobo, a single account holds at most 20 KOReader identities.
