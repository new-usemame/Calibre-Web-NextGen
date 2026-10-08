### Fixed

- **Log lines are no longer lost when a log rotation fails partway on newer
  Python.** If the log directory stops being writable in the middle of a
  rotation, the line being written still reaches the log through the shared
  output, as it already did on older Python releases. Python 3.13.16 changed
  where that error is handled, which silently dropped the line.
