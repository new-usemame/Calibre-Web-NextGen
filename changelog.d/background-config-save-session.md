### Fixed

- **Pages no longer fail with a server error for a moment right after the
  server starts.** At startup, the Kobo KEPUB check (on by default) saved its
  progress through the database session that web pages were using at that
  moment. A page, OPDS feed or Kobo sync request that arrived during the save
  could fail with a 500, logged as "This session is in 'prepared' state".
  Background tasks now save through a session of their own. The same fix
  covers the Kobo KEPUB repair, the download record written by auto-send, and
  Send to eReader, which could e-mail the library cover instead of your
  personal cover if a page was saving at the same moment. The KEPUB repair now
  reports a completion marker it could not save as a failure and runs again on
  the next start, instead of claiming success.
