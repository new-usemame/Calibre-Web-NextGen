### Added

- **Books can be queued to a specific KOReader device from their detail page.**
  The reader collects it on its next sync, in the best format that reader can
  actually open, retries an interrupted transfer without leaving a duplicate
  behind, and skips a book already present in that device's library. A book with
  no format the device can read is refused at the point you queue it, naming the
  formats that were available, rather than failing later on the device.
