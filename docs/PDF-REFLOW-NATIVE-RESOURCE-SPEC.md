# Native resource admission — implementation contract

An accepted conversion must either obtain the same exclusive heavy-work lease
as quote preparation and survey, or fail explicitly before launching its native
child. This is a filesystem flock shared by service processes using the same
Reflow state directory, not a new queue. The child inherits the descriptor:
parent death cannot admit new work while that child is still alive. Never unlink
the lock file or use PID staleness guesses. Normal cleanup reaps owned processes
before closing the lease. Busy refusal is immediate; already queued jobs keep
their normal terminal/error and ownership behavior.

Checked mode (default) requires three successful headroom samples before launch.
Linux measurement is the minimum of MemAvailable and every visible applicable
cgroup-v2 memory.max minus memory.current (ancestor limits included). Missing,
malformed or unsupported measurement fails closed. An explicitly configured
serialize-only development/deployment mode retains exclusivity but makes no RAM
claim. Defaults: admission 2048 MiB, stop reserve 512 MiB, scratch admission
2048 MiB, scratch stop 256 MiB; operator values must remain positive and stop
must be below admission. These are campaign-derived conservative thresholds,
not universal guarantees or a per-child limit.

Check reserve during native waits and at parent operation/provider/publication
stop checkpoints; latch failure and use existing owned cancellation cleanup.
No background service, provider replay, ledger release, speculative SIGBUS fix,
or live resource-exhaustion experiment. Sampling cannot beat all sudden OOMs.

Behavioral gate: no launch on low/unknown headroom; shared paths cannot overlap;
cross-process busy/death/inherited-child lifetime; cancellation/refusal releases;
low reserve stops only owned work; Task prior EPUB and financial evidence survive;
success still emits validated EPUB. Use injected readings and tiny real subprocess
fixtures, existing Task tests, focused mutation red/green and one final Reflow suite.
Root owns independent review and actual deployment validation after composition.
