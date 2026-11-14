### Fixed

- **A Kobo's queued report no longer undoes a newer place from the web reader or KOReader.** A Kobo that had been sent another reader's newer place could then send a report it had queued before that, repeating its old place, and the server took it as a deliberate move back. The Kobo then stayed behind, and the web reader reopened at the Kobo's old place. A report the Kobo clocked before it was sent the newer place now keeps that place; going back to an old place after receiving the new one still counts as a move. (#2783)
