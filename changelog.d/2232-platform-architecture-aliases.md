### Fixed

- **Windows and macOS processor names no longer cause a false architecture warning.** Recognize AMD64/ARM64 and arm64 as the existing supported 64-bit CPU families, while retaining warnings for other architectures. (#2232, reported by @Rol3333)
