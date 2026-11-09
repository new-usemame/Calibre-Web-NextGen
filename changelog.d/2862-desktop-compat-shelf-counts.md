### Fixed

- **Desktop compatibility mode no longer recounts every Magic Shelf on every request.** With `DESKTOP_COMPAT_MODE=true`, each page, cover and API request threw away the sidebar's Magic Shelf counts and ran every shelf's count query again, which on a large library held requests for tens of seconds. The counts are now kept until `metadata.db` changes on disk, so a book added in Calibre desktop still shows up in the counts on the next request. (#2862)
