### Fixed

- **A highlight synced from KOReader is now attributed to the device it came
  from.** The Highlights page read only the manual override, never the origin
  the sync recorded, so a freshly synced highlight said "Unknown device" even
  though the server knew better. Assigning one by hand then answered "Assigned
  to Deleted device" — over a write that had succeeded — because the page's
  name lookup was built per book while the dropdown offers every device you
  own. The assignment message can also be dismissed now, instead of leaving
  Undo as the only way out of it. Reported by @iroQuai in #2075.
