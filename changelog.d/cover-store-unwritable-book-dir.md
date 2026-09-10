### Fixed

- **Setting a cover no longer fails with "not a valid image file" when the server cannot create files in the book's folder.**
  A folder owned by another user (for example one created by a root process on a network share) blocked every cover
  change since v4.1.43 because the new cover was staged as a sibling file first. When the existing cover itself is
  writable, the server now replaces it in place instead of refusing; when it is not, the error names the folder and
  the user ids involved instead of blaming the image.
