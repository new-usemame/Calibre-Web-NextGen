### Fixed

- **Searching from the top search box is much faster on large libraries.** Each tag, author, series, publisher and custom-column name is now matched once per search instead of once for every book linked to it. On a 270,000-book test library a one-word search went from 3 to 7 seconds per query to under a second. Results are the same, including accent-insensitive matches such as "cafe" finding "Café" (#2720).
