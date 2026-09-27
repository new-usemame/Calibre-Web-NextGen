### Fixed

- **Removing several files from a KOReader device now takes one sync, not one sync per file.** Marking multiple files for removal on the Devices page and then running "Sync now" in the CWNGSync plugin removed only one of them; each later sync removed one more. The plugin now works through the whole removal queue in a single sync (up to 50 files at a time, with the rest picked up on the next sync). A file the device declines to remove is reported back and no longer holds up the others. Reported in [#2328](https://github.com/new-usemame/Calibre-Web-NextGen/issues/2328) by @magdalar.
