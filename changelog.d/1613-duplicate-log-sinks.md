### Fixed

- **Bare-metal services no longer write each application log record twice when stdout is redirected to the configured log file.** Shared file sinks use one rotating writer, including symlink, hardlink and fallback paths. Separate stdout and file outputs retain both Docker logs and the administrator log viewer. (#1613, reported by @Thovi98)
