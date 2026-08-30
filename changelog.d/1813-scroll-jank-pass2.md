### Fixed

- **Smoother scrolling in long annotation lists, faster typing in the author/tag browser, and page turns no longer stutter the reader's progress bar.** Scroll handling re-renders only when the visible window actually moves, large browse pages defer filtering off the keystroke path and skip off-screen render work, and the progress bar animates on the compositor.
