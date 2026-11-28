### Added

- **The support pack download can now tell the page when it has started.** A page that offers the pack adds a random `dl_token` to the download URL and watches for a short-lived `cwng_pack_token` cookie, which is set only when the download succeeds. It lets the Admin page and the Help menu show "Preparing…" and then "Downloaded" without holding the file in memory, which Firefox's content security policy would block (#2728).
