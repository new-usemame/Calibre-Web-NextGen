### Fixed

- **Conversion choices now match the Calibre installation that will run the job.** The editor lists only formats its installed input and output plugins report, including explicitly enabled user-installed formats such as KFX. Classic and SPA conversion requests both validate against that same capability list; if the local capability probe is unavailable, Calibre formats are hidden instead of being guessed. The separate EPUB-to-KEPUB option remains available when `kepubify` is configured.
