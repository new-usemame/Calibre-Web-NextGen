### Fixed

- **Proxy-created accounts with an email address as their username get a usable profile email.** When the proxy sends an email address as the username, new accounts use that address if it is valid and available instead of adding a second `@localhost` and blocking profile saves. Separate `Remote-Email` and `X-Remote-Email` headers are ignored. An email already held by another account falls back to a placeholder; proxy login matches existing accounts by username only, never by their editable profile email. Thanks to @splitsec2. (#2426)
