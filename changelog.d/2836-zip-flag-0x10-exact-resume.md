### Fixed

- **The web reader opens at the Kobo's exact place for books whose ZIP archive marks its members with the "enhanced deflating" flag.** Such an EPUB was refused as a whole by the exact-place check, so the reader fell back to the percentage and logged "unsupported resume ZIP member encoding" at debug level. Zlib and JSZip both ignore that flag for the compression methods accepted here, so it is now allowed. Thanks @sgreadly for finding the cause and writing the fix. (#2836, #2837)
