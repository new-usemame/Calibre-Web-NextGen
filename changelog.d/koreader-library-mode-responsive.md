### Fixed

- **KOReader no longer freezes when you turn on Library mode.** Library mode
  downloaded a placeholder for every book one after another, and KOReader
  read no taps until the last one arrived. On Android that looked like
  "KOReader isn't responding". KOReader now reads taps between downloads.
  Progress is saved every few books, so restarting KOReader no longer starts
  the download over. Placeholders are no longer counted as books on the
  device's page, even after a restart. Turning Library mode on from the menu
  now leaves your home folder alone, and turning it off restores the home
  folder settings that setup changed. Reported by @magdalar (#2329).
