### Fixed

- **A highlight sent to the Hardcover journal is tagged with the reader it came from, and shows its page.** Every journal entry got the tags `#CWA` and `#Kobo`, so a KOReader or web reader highlight also said Kobo. The second tag now names the source as the Annotations page does (`Kobo`, `KOReader`, `Web reader` or `CrossPoint`), and a source with no known reader gets only `#CWA`. A highlight from KOReader or CrossPoint, or any highlight with a KOReader position, went with no position and showed page 0. CWNG now sends its place in the book, and Hardcover shows the page from it.
