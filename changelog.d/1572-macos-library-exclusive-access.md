### Changed

- **Use exclusive library access with native Mac Calibre and Docker Desktop.** Stop the NextGen container before opening its host-bind-mounted library in Mac Calibre or native `calibredb`, then fully quit Calibre before restarting the container. The desktop compatibility flags do not protect simultaneous writes across this boundary. Clarifies the setup advice while the exact ingest crash reported in #1572 remains under investigation.
