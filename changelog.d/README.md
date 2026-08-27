# Changelog fragments

Every pull request that changes shipping code or assets records its release-note
entry in a separate Markdown file under this directory. That keeps concurrent
branches away from the shared `CHANGELOG.md` insertion point.

A fragment is not required when every changed path is confined to `findings/`,
`notes/`, `docs/`, `tests/`, `frontend/e2e/`, this `changelog.d/README.md` file,
or the guard implementation itself at `scripts/check_changelog_diff.py`. Those
paths contain evidence ledgers, working notes, documentation, verification code,
or CI policy rather than application behavior. This is an all-paths exemption:
a pull request that mixes any of them with shipping code or assets still needs
a fragment. Unlisted paths require a fragment by default.

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
This `README.md` is documentation and does not count as a fragment. It is only
exempt when every other changed path is also non-shipping as described above.

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
