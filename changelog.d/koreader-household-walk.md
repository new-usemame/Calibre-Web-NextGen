### Fixed

- **KOReader: a device moved to another account no longer shows the first account's books.** Connecting an e-reader to a different CWNG account now moves the previous account's downloaded and sent books out of the library folder, with their positions and notes, into a folder named after that account, and says where they went. Their names, which carry the other server's book numbers, can no longer block the new account's books.
- **KOReader: connecting from the menu ends on the library.** The menu the connection started from used to stay on screen, still offering "Connect this device", after the device had connected.
- **KOReader: the pairing screen gives an https server's address with https.** An address shown without it sends a browser to plain http, which an https-only server refuses.
