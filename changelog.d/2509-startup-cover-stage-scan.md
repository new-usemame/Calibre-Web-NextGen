### Fixed

- **A large library no longer delays startup while CWNG looks for half-written covers (#2509).** The crash-recovery cleanup for interrupted cover saves used to read every book folder before the web server started; on a 129,000-book library on ZFS that kept the server down for about 36 minutes after a restart. Each cover save now records where its temporary file is, so startup removes only the ones a crash actually left behind. Leftovers from earlier versions are cleaned once, in the background, after the server is up, and that sweep never touches a cover being saved.
