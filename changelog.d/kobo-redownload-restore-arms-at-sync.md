### Fixed

- **The post-download highlight restore on a Kobo now actually engages.** The device fetches the book file without identifying itself, so the download alone could not tell the server which Kobo had just emptied its highlights. The restore is now armed by the sync that re-sends a book the device already holds, the download is attributed to the Kobo that just synced, and an annotation request made while the restore is armed is answered from the server's own highlights whether it arrives before or after the file download.
