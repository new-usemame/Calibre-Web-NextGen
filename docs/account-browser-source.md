# Browser reading sources

Browser reading is one source per user account, shared across computers, browser profiles, private sessions, and the classic and modern readers. It appears after reading progress or annotations have been saved. Each physical Kobo or KOReader device remains its own source.

Upgrades consolidate previously separate browser sources automatically. Existing annotations and notes keep their IDs, content, locations, timestamps, and revisions. Explicit assignments to physical devices remain intact. Shared browser bookmarks and resolved Kobo progress are not rewritten by consolidation. The Browser activity list uses the latest complete per-book observation, preserving its matching position and percentage.

Old browser-source links and saved source IDs resolve to the account’s shared Browser source. Previous source rows and their transport history remain internally as aliases; they are excluded from device lists and assignment choices. The oldest active source is preferred as the canonical source, and a custom name on that source is preserved. If all sources were removed, they remain removed until new browser activity or an explicit restore. Removing Browser keeps reading data and reuses the same source on the next reading save; it does not implicitly restore cleared device assignments.

Older releases without source records gain a Browser source from existing browser annotations or bookmarks. Accounts with no browser reading activity do not gain one just from logging in. Legacy installation headers are accepted but do not influence source identity.

The migration runs in one transaction and is repeatable. A database uniqueness constraint prevents concurrent first writes from creating duplicate account sources. Before upgrading, retain the usual configuration/database backup. Rolling back to a build with per-installation sources requires restoring its matching pre-upgrade app.db backup as well as the old application image.
