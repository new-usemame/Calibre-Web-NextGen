### Fixed

- **A log rotating while the support pack builds no longer fails the download, and the size cap holds on what is written.** A log that rotated away between the directory listing and the read used to abort the whole pack; it is now skipped and the manifest records each log as included, missing or unreadable. Logs are read from the tail at the file handle instead of whole, the 10 MiB cap now applies to the final bytes after scrubbing and the omission note (scrubbing can lengthen lines), and the traceback digest is built from the same bytes that go into the archive (#2728).
