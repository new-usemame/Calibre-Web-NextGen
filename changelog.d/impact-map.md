### Added

- **Contributors can ask what a change under `cps/` could affect, and get an
  answer that admits what it cannot see.** A static impact map joins the Python
  call graph to the reconciled Flask route surface, so a file, symbol, or route
  query returns what reaches it and what it reaches, with a confidence on every
  hop. Edges resolved through an import binding are never merged with ones
  guessed from an attribute name, and every call site the analysis could not
  resolve is emitted as data with its location and reason — so the answer to
  "how blind is this about module M?" is a number rather than a shrug. The map
  states its own limits in the same breath: it stops at Python, and roughly a
  third of real changes also touch a template or the frontend, where it has no
  nodes at all.
