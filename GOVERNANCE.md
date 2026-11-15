# Governance

## Stewardship

- **Project lead**: [@new-usemame](https://github.com/new-usemame). Final call on releases, security policy, and commit access.
- **Maintainers**: trusted contributors with commit + merge rights. Earned through ~3 quality PRs + a clean track record. Mostly merge in their own subsystems; escalate cross-cutting changes to the project lead.
- **Contributors**: anyone with a PR. Credited by handle in release notes.

## Relationship to upstream

This fork exists because review on `crocodilestick/Calibre-Web-Automated` has been paused and the PR + issue queue accumulated. The fork is a **continuation, not a takeover**. Operating principles:

- We do not own upstream. We comment on existing threads, never edit, never close.
- Every backported patch is mergeable back if upstream review resumes.
- Original PR authors are credited by handle in release notes.
- If the upstream maintainer returns and wants to coordinate, repo is public, branches are public, all squash-merge SHAs trace to upstream PR numbers in their commit messages.

## Becoming a maintainer

1. Open ~3 PRs that get merged without major rewrites.
2. Ask in an issue or DM `@new-usemame`.
3. If accepted, you get write on the repo + the `maintainer` GitHub team. No paperwork, no CLA.

If you previously had a PR ghosted on upstream and want commit access here to keep your work moving — reach out. That's exactly what this fork is for.

## Decision process

- **Bug fixes / security**: any maintainer can merge under the tier policy in `CLAUDE.md`. Security fixes follow the disclosure flow in `SECURITY.md`.
- **Features / refactors / dep changes**: project lead approval required. Open as a PR with a design note in the description (or a markdown file under `notes/`).
- **Disagreements**: discuss in the PR or an issue. If unresolved after a week, project lead decides. Decisions are reversible with new evidence.

## Succession

If `@new-usemame` is unreachable for 90 consecutive days (no commits, no responses on issues, no responses to direct contact):

1. The longest-tenured active maintainer becomes acting project lead.
2. They coordinate with other active maintainers to confirm the role within 2 weeks.
3. The fork's repo settings (admin, secrets, package permissions) get rotated — see `notes/SUCCESSION-PLAYBOOK.md` *(to be written when there's a second maintainer)*.

The fork should never go dark just because one person stops showing up. That's the whole point.

## Code of conduct

Be respectful in PRs, issues, and Discussions. No personal attacks, no harassment, no off-topic political flame wars. Disagree on the technical question, not the person. Project lead can lock or remove abusive comments and ban repeat offenders. The full text, and how to report something, is in [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

Specific to this fork: **no hostility toward upstream or its maintainer**. We are guests in their ecosystem. If you can't keep that tone, don't post here.

## License

Same as upstream CWA (GPL-3.0). Contributions are accepted under the same license. No CLA.
