### Changed

- **The KOSync reference for third-party apps now covers everything past the stock protocol.** `cps/progress_syncing/README.md` documents the `capabilities` list on `/kosync/users/auth`, the `X-CWNG-Device-ID` and `X-CWNG-Device-Name` headers and how they change which place wins (latest by the app's clock instead of furthest), the 20-device limit and the 409 `device_inactive` reply, pushing a place as a percentage with the words at it, and the highlight sync wire (`text_quote`, named deletions, `book_known` and `matched: false`), each with a working curl example (#2848).
