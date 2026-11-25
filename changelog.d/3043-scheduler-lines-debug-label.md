### Fixed

- **Routine scheduled-job lines are now labelled DEBUG instead of INFO (#3043).** The "Running job" and "executed successfully" lines only appear when the log level is DEBUG, but they were printed with an INFO label, so filtering the log by level got them wrong. Missed and failed runs keep their WARNING and ERROR labels.
