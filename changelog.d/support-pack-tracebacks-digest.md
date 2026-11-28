### Added

- **The support pack now has a `tracebacks.txt` digest.** In a long log the tracebacks are a handful of lines among thousands, so the digest lifts them out: one entry per place an exception was raised, with how often and when it was seen and the most recent full traceback, most recently seen first. It is built from the same scrubbed text as the logs, so it holds nothing they do not, and it says so plainly when there are no tracebacks (#2728).
