### Fixed

- **An account created by reverse-proxy login can now set its first password from Account in the new interface.** These accounts start with no password, and the form refused every attempt with "Current password is incorrect", so they could never set one for OPDS or KOReader sign-in. The form now asks only for the new password until the account has one, as the classic profile page already allowed. Reported by @splitsec2 in #2572.
