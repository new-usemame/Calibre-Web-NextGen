### Fixed

- **A Kobo being set up for the first time syncs instead of stopping after it connects.** Current Kobo firmware asks the server to add the device before its first library sync. CWNG answered that request with "not found", and the Kobo then cancelled the whole sync. CWNG now answers it, and the device's token refresh, the same way it answers the device login: passed to the Kobo Store when store proxying is on, answered by CWNG otherwise. Reported by @orcofthedarkness (Discussion #2781); the same fix landed in Calibre-Web Automated as 93d5b985d (CWA #1476).
