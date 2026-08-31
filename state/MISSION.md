# Mission: Make the SPA default and persist catalog choices per user

Updated: 2026-08-28  Phase: complete  Status: 10/10 outcomes observed

## Definition of done

- [x] Required project authority, code index, stale M3 briefing, capability/routing, git-manager, run-to-done, and accessibility guidance consumed.
- [x] Cookie-less browser navigation to `/` selects the SPA; standard/OAuth GET `/login` selects the SPA while LDAP and reverse-proxy-header login remain Classic until #1893/#1931 land.
- [x] Classic opt-out sticks across requests/restarts and repeated classic↔SPA round trips; feedback popup remains one-shot and the classic nudge is removed.
- [x] Machine-client requests (missing or wildcard Accept, curl/wget, OPDS UA, Kobo routes) have unchanged status/redirect behavior; explicit browser navigation and reverse-proxy subpaths work.
- [x] Generic named per-user JSON preference API/client facility exists without a migration and safely validates/rolls back.
- [x] Discover, Show hidden books, and per-card action-row preferences are server-authoritative for authenticated users, adopt existing local values once, work across browsers/logout/localStorage clearing, and stay local-only for anonymous users.
- [x] The removed Classic nudge's #1310/#1311 regression suite now guards complete removal of its markup, class, CSS variable, and storage key; stale design documentation is gone.
- [x] UI-routing changes in `cps/spa.py`, `cps/web.py`, and `cps/templates/layout.html` widen the frontend/E2E CI classifier; the base-commit fails-closed design remains intact.
- [x] Objective-specific tests are demonstrated red on the origin/main base and green on the branch; touched Python suites and frontend build/typecheck pass. The touched e2e spec did NOT pass on its first CI execution: the no-JS fallback's login branch asserted the SPA's login field, which cannot render with JavaScript disabled, so it could never have gone green on a login-required server. Corrected to Classic's own `input#username`; that branch had never been executed locally.
- [x] Live local HTTP/browser flow, adjacent regression pass, changelog fragment, commits, and pushed final HEAD are recorded with OBSERVED/ASSUMED evidence.

## Now / next action

All three deliverables are merged and verified as ancestors of `origin/main`: #1956 (928b715986,
new-UI default + per-account catalog prefs), #2023 (2dd1df61b7, only an explicit action revokes a
Classic opt-out), #2021 (194b9c1937, F-994ad6 CI-wedge finding). The parity inventory is filed as
issue #1955. CARE deployed dev build 2b0d098 to the household canary (merge-base verified to
contain both product SHAs).

Open follow-ons, none authorized by this brief: #1955 parity closure gates any classic retirement;
#1959 (SPA cannot surface server-side flashed notices); F-adaa84 (sub-path e2e lane has never run
in CI). Post-compaction hydration record: `state/HYDRATION-2026-08-30.md` — note the operator's
free-text ruling recovered there (stop exposing entry points to classic pages that have a new-UI
equivalent, once parity is proven).

## How to build/run/verify

- `python -m pytest <touched unit suites>`
- `cd frontend && npm run build`
- `cd frontend && npm run test:e2e -- <touched specs>`
- Local Docker/dev HTTP and browser verification using the repository harness.

## Decisions & rationale

- 2026-08-28: implement inline as Sol; no delegate fleet, matching current model-routing doctrine and the operator's implementer assignment.
- 2026-08-28: reuse `User.view_settings`; no schema migration.
- 2026-08-28: build a generic named boolean-preference facility and wire Discover, Show hidden books, and card-actions visibility; keep density and rows per load per-device as directed.
- 2026-08-28: remove the classic opt-in nudge and retain only the plain new-UI nav affordance plus one-shot departure feedback.
- 2026-08-28: M3 briefing is stale since 2026-06-12; treat it as historical and rely on the operator-supplied branch/objectives plus current code.
- 2026-08-28: use `cwng_prefer_classic=1` as the opt-out; continue stamping/deleting legacy `cwng_prefer_spa` for downgrade compatibility.
- 2026-08-28: browser routing requires an explicit positive `text/html` media range and rejects stated non-document/non-navigation Fetch Metadata.
- 2026-08-28: `/me.preferences` returns bool-or-null; `null` uniquely means eligible for one-time localStorage adoption.
- 2026-08-28: mutations use one allowlisted boolean map, one endpoint-owned transaction, optimistic `/me` cache writes, serialized requests, and rollback.
- 2026-08-28: coalesce simultaneous first-load adoptions by account into one mutation, preventing scoped client mutations or concurrent JSON updates from dropping a sibling preference.
- 2026-08-28: explicitly empty the unique-secondary-user Playwright context's inherited project storage; an admin's local preference must not become a new account's adoption input.
- 2026-08-28: widen, never narrow, the E2E classifier. The workflow still executes the base commit's classifier, so a PR cannot edit the classifier to evade its own gate.

## Deliberately out of scope

- Grid density and rows per load remain local, screen-dependent settings. No migration and no new dependency were added. No PR, merge, tag, or `main` change was made.

## Verification record

- Objective 1 red: product held at `a7553332a` with the new regression suite produced 7 failures and 22 passes; cookie-less `/`, cookie-less `/login`, Classic clearing, and removed-nudge expectations failed.
- Objective 2 red: product held at `a7553332a` with `test_named_user_preferences.py` produced 12 failures; `/me`, update endpoint, storage helpers, and client hook were absent.
- Follow-up auth carve-out red-before-implementation: 6 failures and 31 passes; standard/OAuth SPA and LDAP/proxy Classic route/predicate cases are now green. A post-implementation mutation that incorrectly gated authenticated `/` produced 2 failures, proving the guard is login-only.
- Follow-up preference expansion red-before-implementation: 8 failures and 15 passes in the expanded Python suite; the old local-only E2E failed to observe server adoption. A CI-mode E2E later exposed simultaneous adoption dropping Show hidden books (7 passed, 2 failed); the coalesced version passes 9/9.
- Post-implementation guards were made red deliberately: removed-banner token mutation 4/4 failed; malformed/missing/staging/null preference-store mutations failed 5 selected tests; removing the optimistic `/me` cache write failed the held-request browser test; restoring the old SPA cookie made the scoped Classic E2E fail.
- CI classifier red-before-implementation: `cps/spa.py`, `cps/web.py`, and `cps/templates/layout.html` each classified `frontend=false`; all now classify true while an unrelated Classic template remains false. Classifier suite: 27/27.
- Python green: focused changed-surface coverage suite 146/146; complete `python -m pytest tests/unit -q` 7,465 passed, 94 skipped, 0 failed in 221.85s. The known collection-lane timeout test passed there in 34.10s and twice in isolation (33.03s, 31.86s).
- Changed-surface coverage, before hardening -> after hardening: `cps/api/account.py` lines 157/252 (62.30%) -> 160/252 (63.49%), branches 56/108 (51.85%) -> 56/108 (51.85%); `cps/api/serializers.py` lines 65/95 (68.42%) -> 65/95 (68.42%), branches 11/24 (45.83%) -> 11/24 (45.83%); `cps/spa.py` lines 91/98 (92.86%) -> 92/98 (93.88%), branches 17/22 (77.27%) -> 18/22 (81.82%); `cps/ub.py` lines 971/2306 (42.11%) -> 984/2306 (42.67%), branches 25/478 (5.23%) -> 30/478 (6.28%); `cps/user_preferences.py` lines 17/18 (94.44%) -> 18/18 (100%), branches 5/6 (83.33%) -> 6/6 (100%); `cps/web.py` lines 327/2075 (15.76%) -> 337/2075 (16.24%), branches 8/696 (1.15%) -> 13/696 (1.87%). These are file-level changed-surface numbers, not a whole-repository percentage.
- Frontend green: dependency-free preference unit suite 7/7; production `tsc -b && vite build`, 1906 modules transformed.
- Browser green: final Discover preference spec 9/9; all touched/adjacent specs 49 passed and 2 intentional mobile Classic skips. The secondary-context isolation defect was demonstrated red before its fixture fix and the exact desktop test then passed 2/2 including setup.
- Reverse-proxy green: temporary nginx `/cwa/` rig, 6/6 subpath Playwright tests passed after correcting the stale `Duplicate books` selector to the live accessible name `Duplicates`.
- Live base→branch HTTP matrix: missing Accept 200→200; curl wildcard 200→200; wget wildcard 200→200; OPDS-reader root 200→200; `/opds` 200→200; invalid-token Kobo sync 404→404; browser `/` and `/login` 200→302 `/app/`.
- Live guest browser: anonymous identity true, local hidden state applied, show toggle persisted local `0`, and zero preference POSTs.
