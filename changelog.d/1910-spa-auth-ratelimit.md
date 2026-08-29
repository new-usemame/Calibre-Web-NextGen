### Security

- **SPA login, registration, magic-link, and password-reset requests now enforce their declared rate limits.** Successful password logins clear the caller's login buckets so legitimate users are not locked out by earlier attempts. Magic-link polling is sized for four full 10-minute sessions per shared address, and the browser stops with a visible error instead of silently retrying a fatal response.
