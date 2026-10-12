### Changed

- **The Browser reader joins Share my place behind the rollout switch, which remains off by default.** When enabled, an unchecked Browser keeps its own place and read status; a checked Browser shares the latest place, including a move back, and resumes automatically at a newer exact shared place. Browser EPUB places can reach a Kobo exactly when the books align and the Kobo has proven its current KEPUB download. When the switch is enabled, the Devices page lists the account's web reader and shows a sharing control for each device.

### Fixed

- **A web-reader place kept private no longer reaches KOReader.** After "Read from here", a later save from the web reader at the same percentage as the place it last shared could give KOReader that private spot as its exact place. Leaving the reader within a second of "Read from here" could also share the place. KOReader now gets an exact place only for the web reader's place that was actually shared, and the reader keeps the choice when it closes.
