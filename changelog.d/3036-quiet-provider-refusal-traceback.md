### Fixed

- **A metadata or cover source refusing a search no longer prints a traceback in the log (#3036).** When a source such as bol.com answers 403, the cover editor and metadata search still show it as blocked, but the log now carries only the one-line warning instead of a full gevent traceback.
