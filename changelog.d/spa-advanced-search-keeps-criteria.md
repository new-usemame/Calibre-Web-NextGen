### Fixed

- **New UI Advanced Search no longer hides books on libraries with a Yes/No custom column.** The search sent no value for those columns and the server read the missing value as "No", so every search, even an empty one, only returned books with that flag cleared. An untouched Yes/No column now places no constraint on the results. (#2211)
- **An Advanced Search now survives opening a result, going back, and reloading.** The criteria are kept in the page address, so returning from a book or refreshing the tab re-runs the same search, and the address can be bookmarked. Pressing Search again with the same criteria now asks the server again, so edits made in another tab show up. (#2211)
