### Fixed

- **A failed note edit or removal in the web reader can be retried.** Before, when saving an edited note, removing a note that has no passage, or writing a note about the whole book failed, the reader closed the note box and said nothing, and the old note came back next time. Now the box stays open with your text, the reader says the save failed, and pressing Save or Remove again sends the same change (#2778).
