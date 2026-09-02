### Fixed

- **Highlights on library-owned books can no longer be replaced by Kobo's
  stale copy when the library lookup fails mid-sync.** Calibre-Web replays its
  current complete snapshot or asks the device to retry instead of forwarding
  a cloud response that may be missing newer highlights and notes.
