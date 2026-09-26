### Fixed

- **Kobo sync and sign-in keep working when the rate limiter's external store is down.**
  With the limiter pointed at Redis or Memcached, an outage of that store made
  every Kobo sync fail with "too many requests", and a web or Kobo sign-in whose
  password was right could still end in a server error. Both now log the store
  error and carry on, as the app and OPDS sign-ins already did.
