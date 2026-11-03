### Fixed

- **A password or role changed outside the app takes effect on the next request.** Each account lookup for OPDS Basic Auth, the reverse-proxy header login and signed-in sessions now re-reads the account from app.db. Before, an account loaded by an earlier request kept its old values until a restart: a password set by an admin script was refused while the old one kept working, and a reader whose download role was removed could still download over OPDS.
