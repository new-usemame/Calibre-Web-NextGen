### Fixed

- **ACSM fulfillment uses Calibre import hooks.** Tickets now run installed Calibre import plugins before EPUB/PDF ingest,
  preserving the original ticket and guarded source receipts. Auto-Convert
  applies to the fulfilled book; missing or failed plugins keep the ticket in
  failed books and do not create a raw ACSM library entry. Requires an enabled,
  configured ACSM input plugin supplied by the administrator. Addresses #1228.
