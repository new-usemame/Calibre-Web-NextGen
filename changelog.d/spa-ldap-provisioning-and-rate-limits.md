### Fixed

- **LDAP users can sign into the new interface on their first visit.** Directory accounts are created with the instance's configured defaults, and successful LDAP sign-ins reset the failed-login limits. Incorrect credentials remain rate-limited, and administrators can still disable account creation or password sign-in.
