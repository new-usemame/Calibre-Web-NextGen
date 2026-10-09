### Fixed

- **A finished conversion now says it finished.** Converting a format from the
  book page's Files section shows "EPUB is ready" (or the converter's error)
  when the task ends and adds the new format to the file list, without a page
  refresh. The server log also records each successful conversion, so it no
  longer stops at "starting conversion" and looks stalled.
