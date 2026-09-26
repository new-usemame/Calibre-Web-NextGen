### Fixed

- **Undated books are no longer re-sent to Kobo when the server's Python version or operating system changes.**
  Books with no publication date were described to Kobo slightly differently
  depending on the Python version and operating system running the server, so
  moving an install (for example from a Mac source install to the Docker image)
  made every Kobo re-download those books and lose its place in them. The dates
  are now written the same way everywhere, and a Kobo that last synced with the
  other form keeps its books.
