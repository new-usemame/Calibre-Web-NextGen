### Fixed

- **Installs outside Docker can set their Calibre library from the Database
  Configuration page again.** On a native Windows or bare-metal install the
  "Location of Calibre Database" field was read-only and its folder button did
  nothing, so a first run had no way to point the app at a library short of
  editing `app.db` by hand. The field is now locked only inside the container,
  where the startup library scan picks the library on every boot; elsewhere,
  and in the container when `DISABLE_LIBRARY_AUTOMOUNT=true`, you can type the
  path or browse to it. Reported by @Rol3333 (#2343).
