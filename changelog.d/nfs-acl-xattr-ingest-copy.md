### Fixed

- **Ingesting a book onto an NFSv4 share no longer fails with "[Errno 524]"
  (#2921).** Staging a book or format for import, and the EPUB fixer's backup of
  the original to `processed_books/fixed_originals`, copied the file and then
  tried to write back the share's `system.nfs4_acl` attribute, which NFSv4
  refuses. The copy was complete, but the book was moved to the failed folder.
  These copies now keep the times, permissions and extended attributes the
  destination accepts and skip a refused one, the same way book folder moves do.
