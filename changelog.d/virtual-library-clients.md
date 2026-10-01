### Added

- **Book sources can use more existing download clients.** Connect NZBGet for Usenet, or qBittorrent and Transmission for torrent files and magnets. CWNG waits for the whole download to finish, copies the completed EPUB/PDF through normal ingest, and leaves the client’s files and seeding policy intact. Setup checks credentials, category/label, mapped folders and trusted tracker origins. Requests remain off by default and require administrator grants.

- **Busy download clients and final-directory moves no longer strand requests.** Polling uses scoped response limits; completed peer downloads survive tracker errors, successful NZBGet PAR repair and script warnings can import, and final-directory moves wait without resubmitting. Prowlarr-style descriptor redirects to magnets are validated before client submission.
