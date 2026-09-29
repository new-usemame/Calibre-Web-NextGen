### Fixed

- **Two e-readers set up by copying the koreader folder no longer show up as
  one device.** Copying KOReader's settings from one Kindle or Kobo to another
  also copied its sync ID, so the server merged both e-readers into one device
  and each skipped the other's reading progress as its own. The CWNG Sync
  plugin now notices when its settings came from a different e-reader, gives
  that e-reader its own ID, and says so once. Other KOReader devices keep
  their ID unchanged. E-readers set up by copying before this update still
  share an ID: delete the `["device_id"]` line from `koreader/settings.reader.lua`
  on one of them, as described in #2351. (#2351, reported by @befeil)
