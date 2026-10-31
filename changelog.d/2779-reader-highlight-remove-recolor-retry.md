### Fixed

- **A failed highlight removal or colour change in the web reader can be retried.** Before, when removing a highlight or changing its colour failed, the reader closed the highlight menu and said nothing, so the highlight looked handled while it was still there or still the old colour. Now the reader says the change was not saved and reopens the menu on that highlight, and tapping Remove or the colour again sends the same change (#2779).
