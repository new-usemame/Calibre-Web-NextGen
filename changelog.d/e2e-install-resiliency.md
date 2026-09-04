### Fixed

- **Fully verified pull requests no longer get blocked by a short SPA test
  dependency-install timeout.** The E2E lane now gives cached frontend and
  Playwright setup enough time to survive registry stalls, avoids optional npm
  network passes, and reuses its installed dependency tree for the SPA overlay.
