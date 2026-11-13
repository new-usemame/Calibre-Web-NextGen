### Fixed

- **Uploading a Calibre-exported `.fb2.zip` book no longer fails with "File
  type not allowed".** The upload gate read only the last dotted segment, so
  the double extension was rejected as "zip" even with FB2 uploads enabled.
  It is now normalized to Calibre's canonical `.fbz` before validation and
  ingest staging; allowlists that already accept `fb2` inherit `fbz` once on
  upgrade, and `.fbz` files validate as `application/zip` instead of logging
  an unknown-mimetype error.
- **Uploading a `.docx`, `.odt` or `.flac` file is no longer refused in the
  Docker image when "Check if file extensions match file content" is on.**
  The image's MIME table has no entry for those three default formats, so the
  check had nothing to match the file against. Each upload also logged one
  "Unkown mimetype for Extension" ERROR line per format; those lines are gone.
