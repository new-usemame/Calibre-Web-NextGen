### Fixed

- **Uploading a Calibre-exported `.fb2.zip` book no longer fails with "File
  type not allowed".** The upload gate read only the last dotted segment, so
  the double extension was rejected as "zip" even with FB2 uploads enabled.
  It is now normalized to Calibre's canonical `.fbz` before validation and
  ingest staging; allowlists that already accept `fb2` inherit `fbz` once on
  upgrade, and `.fbz` files validate as `application/zip` instead of logging
  an unknown-mimetype error.
