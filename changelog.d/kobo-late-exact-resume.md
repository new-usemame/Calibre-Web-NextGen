### Fixed

- **Kobo exact resume recovers after a slow conversion.** Completed positions are retained for five minutes, so reopening a book can resume at the exact span even when the first request fell back to a percentage. Requests keep their short deadline and bounded worker admission. Operators can adjust the budget with `CWA_KOBO_RESUME_TIMEOUT_SECONDS` (default: `0.05` seconds).
