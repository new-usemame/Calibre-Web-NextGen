### Fixed

- **The library grid no longer collapses to one card per row after a hard refresh.** A first-layout race could measure the catalog grid while it had no width; the single resolved track that comes back was accepted as a real one-column layout and stuck. Measurements are now rejected until the grid's own width can actually fit its minimum card, and the grid self-heals within a frame if an early read slipped through.
