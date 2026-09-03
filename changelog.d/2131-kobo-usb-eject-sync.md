### Fixed

- **A Kobo reader that was plugged into a computer and then unplugged no
  longer loses its downloaded-book state on the next sync.** The server now
  recognizes books it already sent to that same reader even when the reader
  returns an incomplete or reset sync token after the USB connection.
