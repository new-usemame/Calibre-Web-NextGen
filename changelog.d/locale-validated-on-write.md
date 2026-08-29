### Fixed

- **A broken interface language can no longer lock you out of your own
  settings.** The language stored on your account is used on every page, but
  nothing checked it was one the server actually ships — and if a bad value got
  in, the profile page you would use to fix it was the page that stopped
  working. Pages now fall back to a language you can read, and every place the
  setting can be saved checks it first. Regional tags like `pt-BR` are
  understood rather than refused.
