### Fixed
- **Convert Library starts again after a run was killed.** A run stopped by the OOM killer or a SIGKILL left its lock behind, and every later run said it was already running until the container restarted. The lock now records the run's PID, and a lock whose run is gone is cleared. Thanks @splitsec2 (#2424).
- **Cancelling Convert Library stops the conversion that was running.** Cancel stopped the script but left the current ebook-convert or kepubify going in the background. It is now stopped too.
- **The EPUB Fixer starts again after a run was killed.** Its lock had the same problem; it now records the PID too, and a lock whose run is gone is cleared.
