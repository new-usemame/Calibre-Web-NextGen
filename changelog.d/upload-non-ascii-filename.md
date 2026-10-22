### Fixed

- **Books with non-ASCII filenames are no longer silently discarded on upload.** A file such as `龙蛋.epub` was reduced to `epub` by the ingest staging-name sanitizer, so the file landed without a dot or extension and the ingest service deleted it after the API had already reported success. The staging name now keeps the original extension, so CJK, Cyrillic and other non-ASCII uploads import normally (#2701).
