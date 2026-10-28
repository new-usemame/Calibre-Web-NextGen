### Changed

- **Five error handlers no longer swallow shutdown and interrupt signals.** Handlers in the DNB metadata provider, the OAuth debug log, the stats device detection and the ingest lock caught everything, including `KeyboardInterrupt`, `SystemExit` and gevent's `GreenletExit`. Each now catches only the errors it expects, and a new check stops a bare `except: pass` from coming back (#2644).
