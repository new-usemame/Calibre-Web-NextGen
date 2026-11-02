### Added

- **The new UI can be installed as an app and opens without a connection.**
  Browsers that support installable web apps (including Firefox for Android)
  now offer Install for the new UI. Once it has loaded online, the app opens
  offline or when the server is unreachable behind a reverse proxy, including
  the reader. Sign-in, OIDC/OAuth, the API, Kobo and OPDS are not intercepted.
  Set `CWNG_PWA=0` to turn it off; browsers that installed it then remove it.
