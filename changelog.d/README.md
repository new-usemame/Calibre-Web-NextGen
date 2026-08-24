# Changelog fragments

Every pull request records its release-note entry in a separate Markdown file
under this directory. That keeps concurrent branches away from the shared
`CHANGELOG.md` insertion point.

Name the file after the PR when its number is known, or use a short stable slug:

```text
changelog.d/1860.md
changelog.d/kobo-highlight-safety.md
```

Fragments use the existing Keep a Changelog categories and house style. Each
entry starts with a bold, plain-English description of what someone can see or
do differently:

```markdown
### Fixed

- **Books return to the list they were opened from.** The active filter is
  preserved instead of returning to the library root. Reported by @reader.
```

The accepted categories, rendered in canonical order, are `Added`, `Changed`,
`Deprecated`, `Removed`, `Fixed`, and `Security`. A fragment may contain more
than one category or entry. Fragment names are ASCII letters, digits, dots,
underscores, or hyphens and must be direct `.md` children of `changelog.d/`.
This `README.md` is documentation and does not satisfy the CI requirement.

Do not run the assembler from an ordinary PR: successful assembly consumes the
fragment files. Release preparation runs this exact command before the release
commit is created:

```bash
LC_ALL=C LANG=C python3 scripts/assemble_changelog.py \
  --version vX.Y.Z --date YYYY-MM-DD
```

That command moves the current `[Unreleased]` entries and every fragment into a
single dated version section, leaves `[Unreleased]` empty, and deletes consumed
fragments. Files are read in C-locale filename order and entries are grouped in
the category order above, so repeated runs produce the same bytes. Afterward,
release preparation still updates `VERSION` and the in-app What's New data,
runs the changelog tests, commits all of those changes together, and executes
the existing pre-tag release gate.

Direct edits to `CHANGELOG.md` remain CI-valid during the cutover and the
`merge=union` attribute remains as a safety net for branches opened before
fragments were adopted. New pull requests should use fragments.
