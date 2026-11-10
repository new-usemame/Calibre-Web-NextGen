### Added

- **OPDS entries now carry your shared reading place.** For a signed-in account, each book in an OPDS feed has a `<cwng:progress>` with the place as a fraction from 0 to 1 (`0.42` is 42%), the same shared place KOReader sync serves for that book (the furthest one, across every copy of the book). A device with "Share my place" off keeps its place to itself, so it never appears here, and guests get none. Apps that do not know the element ignore it (#2849).
