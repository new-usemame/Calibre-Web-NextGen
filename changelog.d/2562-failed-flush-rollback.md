### Fixed

- **The web UI recovers on its own after a "database is locked" error.** When a write to app.db failed while another process held the database, every later page load could fail with an internal server error until the container was restarted. The request that hit the failed write now cleans it up when it finishes, so the pages after it load normally. Reported by @Glennza1962 in #2562; fix by @theluckystrike (#2584).
