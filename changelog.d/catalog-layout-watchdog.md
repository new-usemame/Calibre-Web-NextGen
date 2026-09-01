### Fixed

- **Catalog first-load column regressions are now caught before release.** A
  continuous browser watchdog checks that the rendered grid and the column count
  accepted by virtualization converge on the available-width formula, including
  opt-in Chromium and WebKit runs with deliberately staggered stylesheet and
  JavaScript responses. Grid mutations and resizes timestamp actual healthy/bad
  transitions. Synchronous DOM, CSSOM, declaration, class/dataset, and Typed OM
  hooks evaluate the same healthy/bad invariant predicate immediately before
  and after the browser write; only a truth flip licenses measured time, so
  irrelevant properties and truth-preserving geometry changes cannot turn a
  coincident layout state into a false failure. Bad-to-differently-bad writes
  update diagnostics without splitting the episode. Nested hooks share the
  outer measurement and do no work until the grid exists.
  Asynchronous stylesheet/font/observer notifications remain diagnostic unless
  an exact synchronous state-changing surface brackets them. Measured bad
  durations accumulate across brief flaps without turning healthy stalls into
  failures. Safety-only
  observations remain visible diagnostics but deliberately contribute no
  duration: the small named gap is preferable to inferred or coincident timing
  that can produce both false reds and false greens. Any violation still active
  at settle fails unconditionally.
