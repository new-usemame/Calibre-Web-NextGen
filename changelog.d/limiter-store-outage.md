### Fixed

- **Sign-in and Kobo sync keep working when the rate limiter's external store goes down.**
  With the limiter pointed at Redis or Memcached, an outage of that store made
  every Kobo sync fail with "too many requests", refused web sign-ins with
  "contact your administrator", and could answer a right password with a server
  error. The limits now carry on in the server's own memory until the store
  comes back, so everyone can still sign in and repeated wrong passwords are
  still slowed down.
