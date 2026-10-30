
### Fixed

- **A colon in a title now survives in the Send to eReader attachment name.** With the eReader attachment filename template set, `Dune: Messiah` arrived as `Dune_ Messiah` because the name went through the same cleaner as files written to disk. An emailed attachment is never a file on the server, so its name now keeps the colon; every other unsafe character is still replaced, and OPDS download names and KOReader filename matching are unchanged. Reported by @LightSBM (#2442, #2650), fixed by @sgreadly.
