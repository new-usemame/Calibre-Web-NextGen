### Fixed

- **The Admin page and the Help menu clear the support pack's download-started cookie where the server sets it.** The server now scopes that cookie to the app's mount path instead of the whole site, so when Calibre-Web-NextGen is served under a sub-path (for example `/calibre`) both pages remove it with the same path. Without this the browser would keep a stale cookie for its short lifetime (#2728).
