### Fixed

- **Generating cover thumbnails for a large library can no longer disappear
  partway through the run.** Each cover now has a bounded processing window, so
  a damaged image or stuck filesystem operation cannot hold the only background
  worker forever. The task continues past isolated cover failures, stops after
  three consecutive timeouts indicate a system-wide problem, and its task
  status and logs now finish with honest generated, skipped, and failed cover
  counts.
