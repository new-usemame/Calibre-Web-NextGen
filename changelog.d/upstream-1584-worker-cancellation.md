### Fixed

- **Background tasks keep running after a queued task is cancelled.** Task history cleanup handles cancelled tasks that never started and have no completion time. Backported from [CWA #1584](https://github.com/crocodilestick/Calibre-Web-Automated/pull/1584) by @meska.
