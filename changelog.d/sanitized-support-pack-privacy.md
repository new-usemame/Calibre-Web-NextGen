### Fixed

- **Sanitized support packs hide private settings and older KOReader progress payloads.** SMTP identifiers and configured paths are redacted. New KOReader debug logs record the progress route without printing the request or response body. Both pack types collect only configured logs and their rotations.\n