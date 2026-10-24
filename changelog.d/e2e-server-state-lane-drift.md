### Fixed

- **A pull request branched before the server-state E2E lane existed no longer
  fails CI for naming a Playwright project its own tree predates.** The e2e job
  deliberately checks out the pull-request head while GitHub supplies the
  workflow from the merge commit, so the lane added in #2093 ran against trees
  that never defined `server-state-chromium` and exited `Project(s) … not
  found` after all 541 specs had passed. The step now reads the checked-out
  Playwright config: it runs the lane when the project is defined, skips with an
  explicit notice when neither the project nor the specs it owns are present,
  and still fails loudly when the specs exist without the project.
