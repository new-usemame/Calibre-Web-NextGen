### Fixed

- **The "Session protection triggered" debug line is logged once instead of on every request.** After a browser update changed the User-Agent, a "Remember me" session kept its old identifier, so each request logged the same mismatch and one page load could print dozens of lines. Each distinct mismatch is now reported once; sign-in and session-protection behaviour are unchanged. (#2882)
