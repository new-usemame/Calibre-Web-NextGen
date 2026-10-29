### Fixed

- **An `.acsm` ticket that no plugin fulfilled now says what to check.** When the Calibre import plugins hand the ticket back unchanged, the ingest log names the two causes seen on a real Adobe setup: a plugin zip that was copied but never registered, and DeACSM without a completed Adobe activation. It no longer tells you to place a plugin zip you already placed. Reported by @sgreadly (#1228).
