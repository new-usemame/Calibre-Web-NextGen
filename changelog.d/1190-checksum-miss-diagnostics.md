### Changed

- **"No book found for checksum" now says why it missed.** The same log line used to cover a checksum row whose book no longer exists (for example after a Calibre database rebuild), a library with no checksums registered at all, and a checksum the server never registered. The line now names which of the three it is, once per checksum every five minutes so a device that keeps retrying doesn't flood the log. Sync behaviour is unchanged. (#1190)
