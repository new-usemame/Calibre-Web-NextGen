### Fixed

- **Calibre Web Companion 2.3.1 and other apps that sign in by reading the login page can sign in again.** Since the new web app became the default, opening `/login` the way these apps do landed on the new app's page, which had no CSRF token, so they stopped with "CSRF token not found". The page now carries the token in the standard `<meta name="csrf-token">` tag. Companion 2.3.3 already works around this on its side; this fixes it for builds that haven't updated yet, such as the current F-Droid release.
