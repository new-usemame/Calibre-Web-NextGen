### Changed

- **Signing in with GitHub, Google or a generic OAuth provider no longer depends on the order the
  server happened to build its login routes in.** Provider sign-in routes are now created fresh for
  each application the server constructs, instead of being reused from a single shared set. On a
  normal single-instance install nothing about signing in looks or behaves differently; the fix
  removes a duplicate-registration failure that appeared as soon as the server built its application
  more than once in one process.
