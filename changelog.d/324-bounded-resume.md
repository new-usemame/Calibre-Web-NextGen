### Fixed
- **Books open even when checking a synced reading position stalls.** Exact-position validation now has a short deadline and falls back to the synced percentage. Slow location indexing no longer blocks the first page, and a late result respects any page turn you have already made.
