### Fixed

- **Library tiles no longer flash a grey highlight behind a cover you only touched while scrolling on a phone or tablet.** iOS
  paints its tap highlight on every touch that starts on a book tile — including touches that turn into a scroll and never
  open the book — so the grid flickered while browsing. Tiles now opt out of the tap highlight; tapping a tile still opens
  the book, and the keyboard focus ring is unchanged.
