### Fixed

- **Pasting an image link into the cover picker no longer fails with "Server returned HTTP 403" on hosts such as Wikimedia, and links copied from Google Images now work.** The
  server identifies itself when it checks and downloads a cover, retries with a normal download when a host refuses the
  quick check, and uses the image behind a Google Images results link instead of the results page. When the check itself
  fails, the reason is shown under the field instead of a silently disabled button.
