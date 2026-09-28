### Fixed

- **A local-only account can sign in to the New UI when LDAP cannot.** The
  `/app/` sign-in endpoint now falls back to the stored local password after
  the directory rejects the account or cannot be reached, matching the classic
  `/login` form, so an administrator of an LDAP instance is not locked out
  during a directory outage. Reported by @justemu.
