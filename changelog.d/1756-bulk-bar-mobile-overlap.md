### Fixed

- **The multi-select action bar no longer hides books or the metadata form on
  mobile.** While a selection is active, the book list now reserves bottom
  space matching the bar's real height, so the last cover row always scrolls
  clear of it, and the Edit Metadata panel's fields can never be painted over
  by the control that opened them. Reported by @magdalar in #1756.
