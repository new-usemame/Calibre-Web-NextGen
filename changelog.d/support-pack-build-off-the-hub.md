### Fixed

- **Downloading a support pack no longer freezes the app, and repeated clicks no longer pile up.** The pack was built on the request thread, which on a Raspberry Pi is seconds of work during which no other request is served, and each extra click queued another full build: fifteen clicks meant a reverse-proxy 504 and an app that stayed frozen until restarted. The build now runs off the request thread, and a second request while one is building is answered at once with "already being prepared" (HTTP 429 with `Retry-After`) instead of queueing (#2728).
