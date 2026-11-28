### Changed

- **The sanitised support pack no longer carries routine scheduler lines.** APScheduler's per-run "Running job" and "executed successfully" lines (a job every 5 seconds is 17,280 pairs a day) made up almost all of an older log, so they are now counted in a note at the top of each log file instead, and the size cap is spent on lines worth reading. Building the pack from a log that was 97% those lines went from 8.6 seconds to 0.3. Failures, warnings and missed runs are kept, and the unredacted debug pack still has every line (#2728).
