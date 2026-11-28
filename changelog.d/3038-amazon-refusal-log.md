### Fixed

- **An Amazon search refusal no longer prints a traceback in the debug log (#3038).** When Amazon or Amazon.co.jp answers with an error such as 503, the cover editor and metadata search still show the source as blocked. The log now carries one warning line instead of a full stack trace at debug level.
