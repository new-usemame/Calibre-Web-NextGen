### Fixed

- **The support pack no longer reports "0" for data it could not read.** A locked or unreadable table used to come out as "0 devices" or "0 pending", so a pack could look healthy having read nothing. Those values are now null with an `unavailable` note giving the reason (database locked, table missing, column missing), the manifest marks the section as not ok, and the device list trims itself to the section size cap while keeping its summary instead of being replaced by a truncation marker (#2728).
