### Fixed

- **KOReader: an e-reader keeps sending its reading position after another device has saved one.** On a server with a time zone set (for example `TZ=America/New_York`), the position a device pulled carried a time several hours in the future. The KOReader plugin then took that position for a newer one from another device and kept its own to itself, so a Kindle stopped reporting its place for hours after the web reader or a Kobo had saved one. The time is now the moment the position was saved.
