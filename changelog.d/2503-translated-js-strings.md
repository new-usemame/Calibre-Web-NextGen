### Fixed

- **Buttons work again on pages whose translation contains an apostrophe.**
  A quote in a translated message ended the JavaScript string it was written
  into, so the browser skipped the page's whole script. In French and Italian
  this left the Hardcover match review buttons (Select, Reject, Skip) and the
  EPUB Fixer doing nothing, and broke cancelling scheduled tasks (French) and
  saving a shelf's order (Italian). Every translated string in page scripts is
  now encoded safely. Thanks to @lguerard (#2503).
