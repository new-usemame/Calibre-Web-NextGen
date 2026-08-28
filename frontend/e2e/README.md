# SPA end-to-end harness

Layer 2 of the verification system (see `notes/verify/FAILURE-MODES.md` + `MATRIX.md` in the workspace).
Drives the **real SPA in a running container** across the matrix cells a UI change can break, so the
Class-1 (full-client-flow), mobile-reflow, default-state, and console-error regressions that shipped in
v4.1.x can't ship silently again.

## Run it

The harness expects the app already running. Locally that's `cwn-local`:

```bash
# from repo root: build + start cwn-local if not up
cd ../.. && docker build -t calibre-web-nextgen:local repo && \
  docker compose -f local-dev/docker-compose.local.yml up -d   # serves :8086

cd repo/frontend
npm run test:e2e            # desktop + mobile, against http://localhost:8086
npm run test:e2e:report     # open the HTML report
```

Env knobs: `E2E_BASE_URL` (default `http://localhost:8086`), `E2E_USER`/`E2E_PASS` (default
`admin`/`admin123`), `E2E_SUBPATH_URL` (set to the `cwn-nginx-571` rig `http://localhost:8087` to run the
reverse-proxy project).

In CI, a same-repository PR whose concurrency/engine dependency closure changed runs this suite as a
hard gate against `sha-<PR head>` — the dev-image workflow builds that exact commit and the test workflow
waits for, then pins, its manifest digest. Frontend-only PRs retain the cheaper SPA-overlay route. To run
the lane outside its automatic path classes, use **Actions → Test Suite → Run workflow** with `run_e2e`
enabled. This is a two-dispatch escape hatch for an old ref: first dispatch **Build & Push - Dev - Split
Strategy** with both `ref=<old ref>` and a non-main `branch=` value, then dispatch **Test Suite** for the
same ref with `run_e2e` enabled. Omitting `branch=` while dispatching from `main` advances the floating
`:dev` channel to that old build; the immutable `sha-<commit>` tag needed by E2E is published either way.
Manual E2E fails immediately when that tag was not prepared, rather than waiting for a producer that was
never started or falling back to another backend. Automatic `dev`-branch E2E likewise fails fast with a
stated reason because the dev-image workflow currently produces push images for `main`, not `dev`.

The concurrency set is an intentionally bounded architectural approximation. Local imports are followed
downward from explicit request/engine roots, including the high-write `cps/web.py` and `cps/kobo.py`
surfaces; reverse dependents cannot be discovered by that traversal and must be added as roots. The
two-level cutoff only bounds each root's dependency fan-out—it is not what excludes reverse dependents.
At this revision the derived set is 110 of 215 local Python modules.

## What it covers (projects = matrix axes)

| Project | Axis | Guards |
|---|---|---|
| `setup` | — | logs in once via the real UI, saves session |
| `desktop` | 1280×800 | full flow + a11y baseline |
| `mobile` | 375×667 (chromium emulation) | drawer reachability + scroll-lock (#576), no h-overflow (#288) |
| `subpath` | reverse proxy (opt-in) | assets/nav under a base path (v4.1.1 reader 404, #571) |

Specs: `browse.spec.ts` (grid→detail→reader flow + clean console + default-state), `mobile.spec.ts`
(drawer), `a11y.spec.ts` (WCAG 2.2 AA gate — axe across every route, **fails on any critical OR
serious** violation, `KNOWN` allowlist must stay empty; plus keyboard/focus invariants: skip link,
single `<main>`, no nested-card tab stop, mobile drawer inert/trapped), `subpath.spec.ts` (base-path).
Grow the a11y gate via the `CWNG_a11y` skill.

## Extending it (keep it honest)

- **Add a spec for every UI bug you fix** — it should fail on the pre-fix build and pass after. That's the
  regression contract.
- **No `data-testid` yet** — selectors are role/text/`href`-based on purpose (doubles as a11y pressure). Add
  a `data-testid` only when a flow is genuinely unselectable otherwise.
- **Shrink `KNOWN_CRITICAL`** in `a11y.spec.ts` as the A11Y-AUDIT findings land — the goal is an empty
  allowlist, not a growing one. A quarantined violation is a named debt, never a silenced red.
- **Theme axis** is not live yet (the SPA ships a single dark theme). Add a theme project when `tokens.css`
  gains a light/other `:root` block.

### Backend capability probes

A frontend/full-stack PR can run on a frontend-only or fork lane whose digest-pinned backend predates a
new route. Use `requireRouteCapability` from `e2e/capabilities.ts` in `beforeEach` to probe route presence
with Flask's automatic `OPTIONS` response and skip with an explicit, self-retiring reason:

```ts
await requireRouteCapability(page.request, {
  method: 'DELETE',
  path: '/api/v1/widgets/1',
  name: 'widget deletion',
  pinnedBy: 'tests/unit/test_widgets.py::test_delete_route_is_registered',
});
```

The probe is presence-only: never send a real payload or interpret a handler body in the helper. A present
but incorrect route must run and fail the real spec. Every probe must name an independent Fast Tests test
that pins route registration on every PR; otherwise the skip would create a silent coverage hole.
Use a feature-specific mutation method (`POST`, `PUT`, `PATCH`, or `DELETE`), not `GET`/`HEAD`: the classic
UI catch-all advertises read methods for unknown paths, so treating those as a presence signal would be a
false positive. The helper rejects that ambiguous probe shape loudly.

### A second user in one spec

Import `test` and `expect` from `e2e/fixtures.ts` only in a spec that needs multiple users. Requesting the
`secondaryUser` fixture creates a unique non-admin viewer through the running container's real admin API,
logs it into a separate browser context through the production auth endpoint, confirms that session in the
SPA, and deletes it after the test:

```ts
import { test, expect } from './fixtures';

test('personal state is isolated', async ({ page, secondaryUser }) => {
  // `page` is the unchanged primary admin session; each request context owns
  // its browser context's independent cookie jar.
  const adminMe = await page.request.get('/api/v1/auth/me').then((r) => r.json());
  const otherMe = await secondaryUser.page.request.get('/api/v1/auth/me').then((r) => r.json());
  expect(adminMe.name).not.toBe(otherMe.name);
  expect(adminMe.role.admin).toBe(true);
  expect(otherMe.role.admin).toBe(false);
});
```

The fixture is test-scoped and lazy, so the existing suite creates no extra users and keeps using
`e2e/.auth/state.json` unchanged. Parallel workers receive different usernames and contexts. Multi-user
specs must mutate only per-user resources (personal shelves, hidden/read state, annotations) or use an
already-dedicated book lane and restore it; creating a second account does not make shared catalog writes
safe while the suite is `fullyParallel` with two CI workers.
