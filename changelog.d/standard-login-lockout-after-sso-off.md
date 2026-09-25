### Fixed

- **Turning single sign-on off no longer locks everyone out.** "Disable Standard Login" used to stay in force after the login type was switched back from OAuth, or after the last OAuth provider was turned off, which hid the username/password form and left no way to sign in. The password login is now withheld only while there is an OAuth provider on the login page to use instead, on both the classic and the new login pages. Reported in [discussion #2272](https://github.com/new-usemame/Calibre-Web-NextGen/discussions/2272) by @lgwapnitsky.
