### Security

- **SPA login, registration, magic-link, and password-reset requests now enforce their declared rate limits.** Successful password logins clear the caller's login buckets so legitimate users are not locked out by earlier attempts.
