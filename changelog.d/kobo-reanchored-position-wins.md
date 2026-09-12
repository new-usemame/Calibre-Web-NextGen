### Fixed

- **A Kobo keeps the right reading position after its book is re-converted.**
  When the server has moved a reader's position into the new file, the device
  restating its old spot at the same progress no longer overwrites that repair,
  so the next sync sends the reader back to the same prose instead of the old
  anchor.
