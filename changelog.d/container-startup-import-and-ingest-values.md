### Fixed

- **Container startup now treats persisted settings and Python import paths as
  explicit trust boundaries.** Ingest timing values read from `cwa.db` are
  validated as non-negative decimal integers before the shell uses them, with
  malformed values falling back to their documented defaults. The web and
  first-run units also ignore `PYTHONPATH` and user-site hooks while retaining
  the image's editable application install, so mounted configuration cannot
  unexpectedly replace the `cps` package that starts.
