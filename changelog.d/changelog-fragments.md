### Changed

- **Contributors no longer have to edit the same changelog insertion point in
  every pull request.** Each change now carries an isolated `changelog.d`
  fragment, and release preparation assembles the fragments deterministically.
