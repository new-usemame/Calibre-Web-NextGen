### Changed

- **CI: the E2E browser install step gets 20 minutes, not 10.** When the Ubuntu package mirror is slow, installing the browsers' system packages takes 4 to 14 minutes. On 2026-11-19, 7 of 25 E2E runs failed at the old 10-minute limit while packages were still downloading.
