### Changed

- **KOReader and CrossPoint now see a Kobo's place as a fraction of a percent, not a whole number.** A Kobo reports only whole percents, measured on its own copy of the book. When the Kobo holds the library's current KEPUB, the server now works out the percentage CrossPoint would show at the Kobo's place and shares that instead, so the readers agree and a move within one percent still reaches the other devices. The Kobo itself keeps the value it reported.
