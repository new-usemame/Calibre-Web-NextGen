### Fixed

- **Custom column values containing a slash are browseable again in the new
  UI.** A value like `Photography.B/W` was silently unreachable there: the
  browse API treated the slash as a path separator, so the node could not be
  resolved and the page said there were no books. Values such as these now
  list their books in the new UI exactly as they always did in the classic one.
- **Picking another value in a custom column starts at the top of its book
  list.** Reading page 3 of one value and then choosing a smaller one used to
  leave the new value on a page that did not exist, showing an empty list with
  no way back except reloading the page.
- **A custom column that fails to load says so.** When the browse request
  failed, the new UI reported it as "no books here" and hid the actual problem.
  The real reason is now shown instead.

### Changed

- **Advanced search on a text custom column matches as it always did.** A term
  naming a node in a hierarchical column briefly matched only that node's own
  branch, so searching `Computers` no longer found `Old Computers`. Search is
  back to a case-insensitive partial match across the column, and browsing a
  value is a separate action from searching for one.
