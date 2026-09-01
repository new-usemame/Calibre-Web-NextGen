### Fixed

- **The catalog grid no longer collapses to one book per row in Safari.** The
  windowed catalog now gives each virtual row an explicit copy of the measured
  column layout instead of relying on WebKit to propagate `auto-fill` tracks
  through a CSS subgrid. Chromium and Safari-engine checks now verify the cards'
  rendered row and column positions, not only the healthy parent grid.
