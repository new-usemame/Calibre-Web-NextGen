### Fixed

- **One swipe in the KOReader library turns one page.** With a plugin that keeps its own bar on screen over full-screen views, such as Simple UI, every swipe in the CWNG Sync library skipped two pages (1, 3, 5 ...). The library now tells KOReader it handled the swipe, so the other plugin no longer passes the same swipe on a second time. (#2730)
