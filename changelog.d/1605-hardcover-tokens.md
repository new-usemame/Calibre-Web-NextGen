### Changed

- **The README explains the two Hardcover tokens and the scopes each needs.** The server token only reads (metadata search and Hardcover ID fetching), each reader's own profile token is the one sync writes with, and a scoped token needs `read:catalog` and `read:me`, plus `read:library` and `write:library` for a reader's token. Asked for by @DBendit in #1605 and #2189.
