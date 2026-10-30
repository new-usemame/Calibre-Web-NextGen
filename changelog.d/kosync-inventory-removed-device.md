### Fixed

- **A removed KOReader device no longer causes a 500 on every inventory report.** The reader still resolved to its soft-deleted device row, so storage reporting raised an unhandled "Device was not found for this account" error. The server now answers 409 `device_inactive`, saying the device was removed and no longer syncs, and logs one warning line instead of a traceback (#2765).
