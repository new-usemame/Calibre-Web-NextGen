### Fixed

- **The log no longer gains two lines every 5 seconds.** With book sources (acquisition) enabled, every run of the import queue job was logged as "Running job" and "executed successfully", burying real messages and growing the log files. Successful scheduled runs are now quiet at the default log level; failed or missed runs are still logged, and the DEBUG log level shows every run again. Reported and fixed by @sgreadly in #2673 and #2674.
