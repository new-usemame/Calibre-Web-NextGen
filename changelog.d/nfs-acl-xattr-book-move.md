### Fixed

- **Changing a book's author or title no longer fails with "[Errno 524]" on an
  NFSv4 library share (#2921).** Moving a book to an author that had no folder
  yet copied every file instead of renaming the folder, and the copy tried to
  write back the share's `system.nfs4_acl` attribute, which NFSv4 refuses. The
  folder is now renamed in place. When a move really has to copy (another
  filesystem), times, permissions and every extended attribute the destination
  accepts are kept, a refused one is skipped instead of failing the edit, and a
  copy that fails part way is removed so no half-moved folder is left behind.
