### Fixed

- **Calibre user plugin registration works again.** Since v4.1.44, with `CWA_CALIBRE_USER_PLUGINS=true`, startup saved new plugins where ingest doesn't look, so ingest never loaded them, even though the log said "Registered Calibre plugin". For example, a configured DeACSM plugin left `.acsm` files unconverted. After updating, these plugins are registered correctly on the next start. Plugins registered before v4.1.44 were not affected. Reported and fixed by @satwell (#2798).
