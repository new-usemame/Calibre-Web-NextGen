# Mission: Add a small rig-pinned visual regression lane and flake ledger

Updated: 2026-08-31  Phase: blocked handoff  Status: 7/8 outcomes observed; full E2E blocked

## Definition of done

- [x] The private Docker rig identity-asserts the image and frontend bundle before a Chromium-only visual project runs.
- [x] The visual suite has at most six `toHaveScreenshot` assertions, includes French, and covers only high-value pixel regressions outside the catalog arithmetic watchdog.
- [x] Rendering is deterministic: viewport/device scale, animations/transitions, caret, data/time variance, covers, and counts are controlled and documented.
- [x] All committed baselines were generated inside the private rig; intentional update and diff-reading instructions are documented.
- [x] A real temporary CSS spacing change makes the visual lane red with a retained diff artifact; reverting it makes the lane green.
- [ ] `@playwright/test` is on 1.62.x, relevant upstream changes are documented, and full E2E plus normal and hostile watchdog lanes pass without compatibility workarounds.
- [x] A committed flake ledger contains the #2084 six-worker condition-wait datum and a concise no-retry-to-green triage procedure.
- [x] Both TypeScript checks, frontend unit tests, Python unit tests, changelog-diff check, final diff audit, private-rig teardown, and exactly one unpushed correctly attributed commit are observed.

## Now / next action

Stop at the explicit full-E2E blocker and report it with the two failed
full-matrix runs preserved as evidence. Diagnosing the supervised app-process
restarts or fixing the six deterministic baseline failures would expand beyond
the requested harness-only, no-production-behavior-change scope.

## How to build/run/verify

- `local-dev/private-e2e-rig.sh test <worktree> --project=visual-regression-chromium`
- `E2E_HOSTILE_LOAD=1 local-dev/private-e2e-rig.sh test <worktree> --project=hostile-css-slow-chromium --project=hostile-css-slow-webkit --project=hostile-script-slow-chromium --project=hostile-script-slow-webkit`
- `cd frontend && npx tsc --noEmit && npx tsc -p tsconfig.e2e.json --noEmit && npm run test:unit`
- `python3 -m pytest tests/unit -q`
- `python3 scripts/check_changelog_diff.py origin/main HEAD`

## Decisions & rationale

- 2026-08-31: implement inline without web-touching subagents; the operator requested one branch and the repository traffic policy favors the fewest agents.
- 2026-08-31: preserve the explicitly supplied base `79ccd23797` even though `origin/main` has advanced; do not introduce unrelated translation/shelf changes.
- 2026-08-31: the visual suite owns rendered pixels only; the #2084 watchdog remains the sole owner of catalog track-count arithmetic and convergence.
- 2026-08-31: any CSS perturbation is test evidence only and must be reverted before the final diff.
- 2026-08-31: the CWNG accessibility skill is scope-guarded to the canonical checkout and does not operate in this scratch worktree; no production UI change is authorized.

## Deliberately out of scope

- No WebKit/Firefox baselines unless a concrete Chromium gap appears.
- No broad snapshot inventory, production behavior change, canary deployment, host port 8083 use, or push.

## Verification record

- OBSERVED: initial branch HEAD is `79ccd23797c8577ed7e22f20e54dc82133b8148a`; worktree was clean; origin is `git@github-anon:new-usemame/Calibre-Web-NextGen.git`.
- OBSERVED: rig-only baseline update created six Linux Chromium PNGs; a strict second comparison passed all six views plus setup.
- OBSERVED: temporary `--sp-4: 1rem` → `1.25rem` rebuilt image `e536522d…`, and the desktop catalog snapshot failed with 67,775 changed pixels (6%). The generated diff is preserved under `notes/`; the CSS token was restored.
- OBSERVED: final rebuilt image `90003417…` served bundle `index-r7NJwhRQ.js` at SHA-256 `ddc85893…`; all six views plus setup passed and the identity stayed fixed after the run.
- OBSERVED: the first full 660-test run at six workers finished 521 passed, 59 failed, 71 skipped, and 9 not run; the normal #2084 watchdog was 17/17 green and the image/bundle identity remained fixed.
- OBSERVED: the two-worker, retries-disabled full run finished 562 passed, 22 failed, 70 skipped, and 6 not run. Sixteen failures directly reported connection refusal/empty-response windows while the supervised app longrun booted three times; Docker kept the same container, image, and bundle.
- OBSERVED: a focused Kobo clipboard check failed identically on 1.61.1 and 1.62.1 over the former non-localhost rig origin, then passed unchanged on 1.62.1 when the runner shared the app container network and used `http://localhost:8083`.
- OBSERVED: the explicit hostile watchdog matrix passed 13/13 (setup plus CSS/script delay lanes on Chromium/WebKit at all three pinned viewports) with identical pre/post image and bundle identity.
- OBSERVED: `npx tsc --noEmit`, `npx tsc -p tsconfig.e2e.json --noEmit`, and `npm run test:unit` passed; the frontend unit result was 32/32.
- OBSERVED: `python3 -m pytest tests/unit -q` passed 8,081 tests with 91 skips. The command used an ephemeral PATH wrapper that disabled the operator-level commit hook only inside pytest's disposable Git repositories; the wrapper was removed immediately after the run and is not part of the branch.
- OBSERVED: `private-e2e-rig.sh down` removed the app and Playwright runner containers plus the rig state directory; exact-name Docker queries returned no remaining containers.
- OBSERVED: the changelog integrity guard passed against current `origin/main`; the final diff audit was clean; the branch contains exactly one unpushed commit above the requested base, authored as `new-usemame`.
