### Fixed

- **A failed highlight or note save in the web reader can be retried.** Before, when saving a new highlight or note failed, the reader said nothing and cleared the selection, so a typed note was lost. Now it says the save failed and keeps the selection and the note, and saving again sends the same highlight. Thanks @jay23606 (#2550, #2554).
