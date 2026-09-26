### Fixed

- **An LDAP server that stops answering no longer freezes CWNG for everyone.**
  Each sign-in that needed the directory used to hold up every other page,
  sync and download until the directory's connection timeout ran out (10
  seconds by default), and one KOReader library sync signs in many times.
  Directory calls now wait on their own, and while the directory is
  unreachable only one sign-in at a time retries it; the rest are told at once
  that it is down, so local-password fallback and "invalid credentials"
  answers come straight back. The first sign-in that reaches the directory
  again restores normal service.
