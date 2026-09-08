### Fixed

- **Keep highlight device selectors reachable.** Highlights and notes now size
  their virtualized rows to the rendered content, including notes, device group
  headers, and selection controls on desktop and touch screens. Long highlights
  no longer overlap the following row or hide their device selector beneath it.
  Large lists still unmount off-screen rows and preserve the visible passage
  when row measurements change.
