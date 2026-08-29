### Fixed

- **An unauthenticated sync request now answers "Unauthorized" instead of "Bad Request".** Every KOSync error was reported with HTTP 400, so a request with missing or wrong credentials came back as a malformed request even though its own body said `Unauthorized` — leaving a reader unable to tell "sign in again" from "that request was broken". Authentication failures now use 401, matching what the rest of the sync endpoints already did; every other error is unchanged.
