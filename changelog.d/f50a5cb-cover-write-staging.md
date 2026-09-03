### Fixed

- **Cover writes are staged and decoded before publication.** A failed write,
  image validation, or metadata commit never touches the existing local or
  Google Drive cover. After a successful metadata commit, local covers publish
  with an atomic rename and existing Drive covers update on the same file ID;
  publication failures trigger metadata compensation.
- A process death between the metadata commit and cover publication can still
  leave metadata claiming a cover that was not published. On the next startup,
  the orphan stage is logged and removed without guessing whether to publish it.
