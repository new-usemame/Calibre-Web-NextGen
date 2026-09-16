### Fixed

- **A PDF-to-EPUB conversion interrupted by an app restart no longer shows as
  "Converting" forever.** Startup now settles the abandoned job's record as
  *Interrupted by a restart*, with the time it stopped, and the Reflow page
  explains that the original PDF is unchanged and that starting again reuses
  the pages already converted — so none of them is paid for twice. Confirmed
  spend and any unconfirmed held charge stay on record, untouched.
