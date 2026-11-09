### Security

- **The sanitised support pack no longer leaks Kobo sync tokens, email addresses or account names.** Logs in the pack now have `/kobo/<token>/` URL tokens replaced, emails replaced with consistent labels (`<email_1>`), and account names from the user table replaced with `<user_1>`, so the same account can still be followed across the pack. If account names cannot be read, the pack says so in `SANITIZED.txt` instead of staying silent.
