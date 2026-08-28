### Fixed

- **Upgrading no longer prints two alarming `no such column: user.has_own_library`
  warnings on the first start.** On a database created before the per-user library
  feature, the migrations that enable the Duplicates and Favorites sidebar entries
  ran before the column they now load was added, so both were skipped with a
  warning that looks like corruption and is not. They applied correctly on the next
  restart, and on a server that already had those sidebar entries there was nothing
  to apply — but on a server old enough to predate them, the two entries stayed off
  until the next restart. Additive column migrations now run before anything reads
  the user table, which also covers the older cover-preview and interface-font
  columns that were exposed to the same ordering hazard.
