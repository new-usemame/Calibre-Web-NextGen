# Contributing

Bug reports, fixes, translations, and small features welcome.

## Reporting a bug

[Open an issue](https://github.com/new-usemame/Calibre-Web-NextGen/issues/new). Useful template:

```
**Version**: v4.0.x (from container env or `/about`)
**Browser / client**: Safari 17 / Chrome 124 / KOReader 2024.04 / Kobo Libra Color
**Reverse proxy**: yes/no, with path prefix?
**Repro steps**:
1. ...
2. ...
**Expected vs actual**: ...
**Container logs (last 50 lines)**: ...
```

Bug reports without a version + repro are still useful — we'll just ask follow-ups before we can act on them.

## Submitting a PR

1. Fork → branch off `main` → commit → push → open PR against `main`.
2. Keep PRs focused on one logical change. Explain the problem, the approach, the user-visible result, and anything deliberately left out.
3. Include verification that traces the real trigger to the observed result. For UI work, name the browser and input modes tested; screenshots are welcome when appearance changed.
4. Add or update tests for changed behavior. Python tests live under `tests/`; React end-to-end tests live under `frontend/e2e/`.
5. CI must pass, including `validate-author`, `Fast Tests (Smoke + Unit)`, `Frontend Build (SPA bundle)`, and `Test Suite Summary`. Docker integration and browser E2E jobs run when the change requires them.
6. If touching `cps/translations/`, update the relevant `.po`; CI validates translations.

### Commit identity and `validate-author`

`validate-author` checks the **committer email on every PR commit**, not just the displayed author. Fork-maintained commits must be committed as `new-usemame` using an allowed noreply address. Genuine outside contributions retain their authorship: a maintainer applies the `community-contribution` label, which exempts that PR from the committer-identity check without weakening its other CI or review gates.

Don't introduce new dependencies, license changes, or external service URLs without flagging in the PR description and tagging `@new-usemame` for approval.

## Local development

The development compose file is a starting point for a live-edit container:

```bash
cp docker-compose.yml.dev docker-compose.override.yml
```

Edit the host paths in `docker-compose.override.yml`, then add source binds under the service's `volumes`:

```yaml
- ./cps:/app/calibre-web-automated/cps
- ./scripts:/app/calibre-web-automated/scripts
```

Start it with:

```bash
docker compose -f docker-compose.override.yml up -d
docker compose -f docker-compose.override.yml logs -f
```

The React application is compiled into `cps/static/app`; mounting `cps/` alone does not compile frontend changes. Build it on the host whenever files under `frontend/` change:

```bash
cd frontend
npm ci
npm run build
cd ..
```

The mounted `cps/` directory then exposes the new bundle to the running container. Stop the environment with `docker compose -f docker-compose.override.yml down`.

## Running tests locally

Install Python development requirements, then run the same fast test class as CI:

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
PYTHONPATH="$PWD:$PWD/scripts" pytest -m "smoke or unit" -n auto --dist=loadfile --maxfail=3 -v --tb=short
```

For a focused change, run its test file directly first. Docker integration tests are sequential by design:

```bash
pytest tests/docker/ tests/integration/ -v --tb=long --durations=10
```

Build and run the React E2E suite with:

```bash
cd frontend
npm ci
npm run build
npx playwright install chromium
E2E_BASE_URL=http://localhost:8083 npm run test:e2e
```

## Backporting an upstream PR

If you spot a useful PR sitting unmerged on `crocodilestick/Calibre-Web-Automated`:

1. `git remote add upstream https://github.com/crocodilestick/Calibre-Web-Automated.git && git fetch upstream pull/<N>/head:upstream-pr-<N>`
2. `git checkout -b merge/upstream-pr-<N>` from current `main`
3. `git cherry-pick upstream-pr-<N>` (or rebase if it conflicts on refreshed `messages.pot`)
4. Re-author the commit so the author email is yours, not upstream's: `git commit --amend --reset-author`
5. Push, open PR, mention the upstream PR number + author in the PR title/body. Release notes will credit `@upstream-author`.

The autopilot script does this automatically (`scripts/draft-cherry-pick.sh <N>`), so check `notes/merge/` first to see if it's already in flight.

## Adopting an unmerged contribution

Useful work from an upstream or community PR may be adopted even when the original PR cannot merge cleanly. Preserve the contributor's authorship when cherry-picking their commit. If the change must be reimplemented, credit the original author and link the source PR in the new commit and PR description; record the credit in `CHANGES-vs-upstream.md` when the change creates or updates a fork divergence. Never use a `Co-Authored-By` trailer for someone who did not author the resulting commit.

## Tier policy (which PRs auto-merge)

`safe-tier-1` (translations / docs only) auto-merge once CI is green.
`safe-tier-2` (≤50 LOC isolated single-file code, no security-adjacent paths) auto-merge after a 7-day clean tier-1 history.
`needs-review` (everything else) waits for project lead.

Full tier definitions in [`CLAUDE.md`](CLAUDE.md#tier-policy).

## Style

Follow the existing code style of the file you're editing. CWA is mostly Flask + SQLAlchemy + jQuery + Bootstrap; we're not doing a rewrite. New code that fits the existing patterns merges; new code that introduces a new framework or paradigm gets bounced.

## Getting commit access

See [`GOVERNANCE.md`](GOVERNANCE.md#becoming-a-maintainer). Short version: ~3 quality merged PRs + ask.

## Credit

Every backported upstream PR credits the original author by handle in the release notes. Direct contributions are credited the same way. We don't squash credit out.
