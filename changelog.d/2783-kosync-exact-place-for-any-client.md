### Fixed

- **KOSync apps that don't ask for percentage positions, such as JustRead, get the Kobo's or web reader's place again.** CWNG held back every place saved by a Kobo or the web reader from an app that did not send `?position_kinds=`, even when it could turn that place into an exact KOReader position in the app's own copy of the book. It now tries that conversion first and sends the exact position; a place it can't convert exactly is still held back, so stock KOReader never gets a position it can't open. Reported by @lukerajah (#2783).
