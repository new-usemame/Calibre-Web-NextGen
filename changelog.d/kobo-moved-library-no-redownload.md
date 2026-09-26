### Fixed

- **Moving your library no longer makes Kobos download their books again.**
  Copying a library to a new disk without keeping file times, or moving the
  server to a new address, used to mark every book on a Kobo as changed the
  next time it synced after an interrupted sync or a shelf edit, and the Kobo
  then downloaded each one again. Books you have really edited, a new cover
  included, still update on the device.
