### Fixed

- **Kobo annotation recovery copies could be deleted by maintenance meant for a
  different folder.** The background job that expires old recovery records
  looked up which folder to clean when it eventually ran, rather than when it
  was scheduled, so a change in between could point it somewhere else. It now
  carries its target with it.
