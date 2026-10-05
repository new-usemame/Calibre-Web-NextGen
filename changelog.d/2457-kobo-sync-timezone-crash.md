### Fixed

- **Kobo sync no longer fails with a server error when a reading position carries a time zone.** Since v4.1.45 some libraries hit `can't compare offset-naive and offset-aware datetimes` on every sync. The sync now puts every reading-state, shelf and archive time on the same UTC basis before advancing its cursor. ([#2457](https://github.com/new-usemame/Calibre-Web-NextGen/issues/2457))
