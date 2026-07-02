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

## What it covers (projects = matrix axes)

| Project | Axis | Guards |
|---|---|---|
| `setup` | — | logs in once via the real UI, saves session |
| `desktop` | 1280×800 | full flow + a11y baseline |
| `mobile` | 375×667 (chromium emulation) | drawer reachability + scroll-lock (#576), no h-overflow (#288) |
| `subpath` | reverse proxy (opt-in) | assets/nav under a base path (v4.1.1 reader 404, #571) |

Specs: `browse.spec.ts` (grid→detail→reader flow + clean console + default-state), `mobile.spec.ts`
(drawer), `a11y.spec.ts` (axe, fails on NEW critical rules; known backlog in `KNOWN_CRITICAL`),
`subpath.spec.ts` (base-path).

## Extending it (keep it honest)

- **Add a spec for every UI bug you fix** — it should fail on the pre-fix build and pass after. That's the
  regression contract.
- **No `data-testid` yet** — selectors are role/text/`href`-based on purpose (doubles as a11y pressure). Add
  a `data-testid` only when a flow is genuinely unselectable otherwise.
- **Shrink `KNOWN_CRITICAL`** in `a11y.spec.ts` as the A11Y-AUDIT findings land — the goal is an empty
  allowlist, not a growing one. A quarantined violation is a named debt, never a silenced red.
- **Theme axis** is not live yet (the SPA ships a single dark theme). Add a theme project when `tokens.css`
  gains a light/other `:root` block.
