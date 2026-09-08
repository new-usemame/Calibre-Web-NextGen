### Fixed

- **Concurrent book downloads no longer fail during metadata embedding.** Calibre exports coordinate with other library operations, and a failed export serves the original file instead of advertising a missing temporary file. Each download keeps its own unique staged filename so another reader cannot overwrite or delete it. KOReader filename matching uses the filename delivered to the client.
