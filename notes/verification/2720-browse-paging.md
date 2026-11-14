# Issue 2720: Authors and Series paging

Implemented on `fix/2720-browse-paging`, 2026-10-27. Work was confined to this
worktree; the Python interpreter and installed browser were existing tools.
The only dependency installation was `npm ci`; no dependencies or external URLs
were added. No skills, vault, external application, push, or schema changes were
used. The supplied paging gate and the prohibited scripts/tests were unchanged.

Authors and Series accept validated paging/name/minimum parameters. Their grouped
SQL queries apply the name predicate and count HAVING before LIMIT/OFFSET and
count grouped IDs through SQL subqueries. The legacy response remains a full
`items` list without `limit`. Literal Unicode name matching uses SQLAlchemy's
SQLite REGEXP, because this project's custom SQL `lower()` also transliterates
accents. Author matching uses the displayed comma in place of stored pipes.

The SPA uses 200-item infinite queries for Authors and Series, a 250 ms name
debounce, and the existing intersection observer with a 200 px margin. Invalid
minimums disable requests and retain the existing inline error. Result status
uses filtered `total` and `unfiltered_total`, and the heading retains the
unfiltered count. Other browse lists and Catalog's full-list hook retain their
behavior. Existing msgids, keyed reset, compact toggle and accessibility
attributes are preserved.

## Verification results and limits

- Requested Python command: **55 passed, 2 failed**. Both failures are the
  supplied gate's `test_filters_apply_before_paging`, at line 94, once per
  endpoint. It expects `q=LE` to match `Delany`, but `delany` contains `el`, not
  `le`. The correct matching list is `Le Guin`, `Leckie`, total 2. With limit 2
  its first page is complete, and offset 2 is empty. The gate was not edited.
- Expanded Python run: **104 passed**. This includes 35 new server contract
  cases, all 45 existing API/Nordic cases, two browse-toggle checks and the
  frontend-suite wrapper's 22 checks. It also exercises all existing Node unit
  suites through the wrapper.
- `tsc -b`: **passed**, including after the final production changes.
- `npm run test:unit`: **230 passed**, including its e2e/unit TypeScript check.
- Focused Node paging/query suites: **7 passed**. They exercise a real React
  Query InfiniteQueryObserver, observer entry/cleanup, filters on every page,
  resetting offset on a filter change, invalid minimums making no requests,
  and preventing collisions with full-list caches.
- Standalone Chromium component check: **passed**. Real scrolling loaded 200,
  400, then 500 authors and stopped. Both paged routes passed debounce, filter
  parameters, header/result counts, inline invalid-minimum error/no requests,
  and recovery. Plural reset, compact persistence and Tags' full-list/local
  filtering passed. No browser page errors occurred.
- `npm run lint`: **unavailable**, because the manifest defines no lint script.
- Vitest: **unavailable**, because the manifest and lockfile contain no Vitest
  dependency/configuration. Tests use the repository's existing Node runner;
  no framework dependency was added.
- Whitespace checks passed. The supplied paging gate and all prohibited paths
  have an empty diff.
- No production build output was generated. No full backend/browser deployment,
  272,000-book benchmark, real-library scrolling, Safari check, or manual screen
  reader check was performed. Browser component checks use mocked API data;
  backend behavior is checked separately with real SQLite.

## Shell command log

Commands below ran from the worktree root unless prefixed with **frontend**.
Polling an asynchronous command's output did not start another command. Edits
were made with the patch tool. Read-only inspection commands are included, as
requested; their output is summarized rather than reproduced.

1. `pwd && rg --files -g AGENTS.md -g 'BrowseList*' -g '*queries*' -g '*browse*' -g 'package.json' -g '2720*' -g '*nordic*' -g '*vitest*'`
   — exit 0; confirmed worktree and located source/tests/manifests; no AGENTS.md found.
2. `git status --short && git branch --show-current && rg --files changelog.d | head -15`
   — exit 0; initially clean, branch `fix/2720-browse-paging`.
3. `sed -n '1,280p' cps/api/browse.py && sed -n '1,300p' frontend/src/pages/BrowseList.tsx`
   — exit 0; read current endpoints and page.
4. `rg -n 'useEntityList|useInfiniteQuery|IntersectionObserver' frontend/src && cat frontend/package.json && rg --files frontend/src | rg '(test|spec)\.' && cat changelog.d/2732.md changelog.d/README.md && sed -n '1,280p' tests/unit/test_2720_browse_paging.py`
   — exit 1; source/manifests read; no tests matched under `frontend/src`, so
   the remaining reads were performed in later commands.
5. `sed -n '1,280p' frontend/src/lib/queries.ts && sed -n '1,260p' tests/unit/test_api_v1_browse.py`
   — exit 0; inspected hooks and existing API mock contracts.
6. `sed -n '190,430p' frontend/src/pages/BrowseList.tsx && rg -n 'useEntityList|useInfiniteQuery' frontend/src/lib/queries.ts && rg -n 'useEntityList|IntersectionObserver' frontend/src --glob '!i18n/**' && cat frontend/package.json`
   — exit 0; confirmed Catalog caller, observer reuse, and missing lint/Vitest.
7. `cat tests/unit/test_2720_browse_paging.py && rg --files frontend | rg '(test|spec)\.|vitest' && cat changelog.d/2732.md changelog.d/1605-hardcover-tokens.md && rg --files -g AGENTS.md -g '*impact*' -g '*IMPACT*'`
   — exit 0; read gate, located actual test directories and changelog examples.
8. `sed -n '150,215p' frontend/src/pages/BrowseList.tsx && sed -n '565,615p' frontend/src/lib/queries.ts && cat frontend/src/lib/useIntersectionObserver.ts && cat frontend/tests/unit/entityListQueryOptions.test.ts && cat frontend/tests/unit/rootUnitLane.test.ts && cat frontend/vite.config.ts && cat frontend/tsconfig.json frontend/tsconfig.app.json frontend/tsconfig.e2e.json`
   — exit 1; useful reads succeeded; `tsconfig.app.json` does not exist.
9. `rg --files -g '*package*json' -g '*eslint*' -g '*vitest*' -g '*test*config*' -g '*pytest*' && ls frontend/node_modules/.bin && sed -n '1,220p' tests/unit/test_1050_nordic_request_collation.py && rg -n 'lower|casefold' cps/db.py cps/unicode_collation.py && cat frontend/src/lib/entityListQueryOptions.ts && cat changelog.d/README.md`
   — exit 1; node_modules absent; subsequent reads repeated below.
10. `sed -n '1,220p' tests/unit/test_1050_nordic_request_collation.py && rg -n 'lower|casefold|_register_sqlite_udfs' cps/db.py cps/unicode_collation.py && cat frontend/src/lib/entityListQueryOptions.ts && cat changelog.d/README.md && cat frontend/tsconfig.node.json && sed -n '1,120p' frontend/src/lib/api.ts && sed -n '1,140p' frontend/unit/apiGetCancellation.test.ts`
    — exit 0; inspected collation, query options, changelog, types and transport.
11. **frontend:** `npm ci --no-audit --no-fund`
    — exit 0; installed 114 locked packages. npm reported existing deprecated
    packages and packages with install scripts; manifest/lockfile unchanged.
12. `../../repo/.venv/bin/python -m pytest -q tests/unit/test_2720_browse_paging.py tests/unit/test_api_v1_browse.py tests/unit/test_1050_nordic_request_collation.py -p no:cacheprovider`
    — exit 1; 55 passed, 2 failed on the gate's erroneous LE/Delany expectation.
13. **frontend:** `./node_modules/.bin/tsc -b && npm run lint`
    — TypeScript passed; overall exit 1 because no lint script exists.
14. `cat frontend/src/lib/i18n.tsx frontend/src/lib/usePersistentBool.ts frontend/src/lib/a11y/announcer.tsx && cat frontend/tests/unit/libModuleResolution.test.ts && cat frontend/playwright.config.ts && sed -n '175,210p' cps/db.py && sed -n '2810,2840p' cps/db.py`
    — exit 0; inspected component context, persistence, harness and SQL functions.
15. `rg -n 'create_function|casefold|unicode_lower' cps/db.py cps | head -70 && git diff --stat && node -v && sed -n '290,355p' frontend/src/pages/BrowseList.tsx && sed -n '1,100p' frontend/src/lib/queryClient.ts`
    — exit 0; checked SQL helpers, diff, sentinel placement and QueryClient;
    Node v26.10.0.
16. **frontend:** `node --input-type=module -e 'import { chromium } from "@playwright/test"; const browser = await chromium.launch({headless:true}); console.log("Chromium launch passed"); await browser.close();'`
    — exit 0; existing Chromium launched successfully without installation.
17. `rg -n 'tests/unit|test:unit|vitest' .github frontend/tests/unit/type* frontend/tests/unit frontend/unit/typecheckChain.test.ts | head -75 && sed -n '230,255p' frontend/src/lib/queries.ts && sed -n '1,80p' frontend/src/unneeded-file 2>/dev/null`
    — exit 1; zsh rejected the unmatched `type*` glob; query excerpt read,
    nonexistent exploratory path produced no output. Correct inspection below.
18. `rg -n 'tests/unit|test:unit|vitest' .github frontend/tests/unit frontend/unit/typecheckChain.test.ts | head -75 && ls tests/unit/__init__.py tests/__init__.py && sed -n '1,50p' frontend/src/lib/queries.ts && rg -n 'EntityList' frontend/src/lib/api.ts && sed -n '1,100p' frontend/unit/pagination.test.ts`
    — exit 0; located CI unit wrapper and Python package fixtures.
19. `cat tests/unit/test_frontend_unit_suites_run.py && sed -n '100,150p' .github/workflows/tests.yml && rg -n 'browser|playwright' tests/unit/test_frontend_unit_suites_run.py && sed -n '380,395p' frontend/src/lib/api.ts`
    — exit 1; read runner/CI configuration; no browser/playwright match in
    the wrapper, so final excerpt was unnecessary and skipped.
20. **frontend:** `node --experimental-strip-types --import ./tests/unit/registerTypeScriptResolution.mjs --test tests/unit/browsePaging.test.ts tests/unit/entityListQueryOptions.test.ts && ./node_modules/.bin/tsc -b`
    — exit 0; 7 tests passed; TypeScript passed.
21. `../../repo/.venv/bin/python -m pytest -q tests/unit/test_2720_browse_contract.py tests/unit/test_2720_browse_paging.py tests/unit/test_api_v1_browse.py tests/unit/test_1050_nordic_request_collation.py -p no:cacheprovider`
    — exit 1; 86 passed, 4 failed. Two were the unchanged gate defect;
    two were a new test's mistaken assumption that MO matches only Moon
    (Asimov also contains mo). That new test was corrected to use MOO.
22. `mkdir -p frontend/.tmp-2720`
    — exit 0; temporary browser harness directory, subsequently removed.
23. **frontend:** `node .tmp-2720/check.mjs`
    — exit 0; all four component-check groups passed. The harness was then
    retained under `frontend/tests/browser` for reproducibility.
24. `../../repo/.venv/bin/python -m pytest -q tests/unit/test_2720_browse_contract.py tests/unit/test_api_v1_browse.py tests/unit/test_1050_nordic_request_collation.py tests/unit/test_697_browse_list_view_toggle.py tests/unit/test_frontend_unit_suites_run.py -p no:cacheprovider`
    — exit 0; 104 passed. Includes the final 35 new server cases.
25. `git diff --check && git diff -- cps/api/browse.py frontend/src/pages/BrowseList.tsx frontend/src/lib/queries.ts frontend/src/lib/entityListQueryOptions.ts frontend/src/lib/useIntersectionObserver.ts && git status --short`
    — exit 0; reviewed implementation and checked whitespace/status.
26. **frontend:** `./node_modules/.bin/tsc -b && npm run test:unit`
    — exit 0; TypeScript passed; 230 frontend unit tests passed.
27. `../../repo/.venv/bin/python -m pytest -q tests/unit/test_2720_browse_paging.py tests/unit/test_api_v1_browse.py tests/unit/test_1050_nordic_request_collation.py -p no:cacheprovider`
    — exit 1; final exact requested gate run: 55 passed, same 2 known failures.
28. `rmdir frontend/.tmp-2720 && git diff --check`
    — exit 0; removed now-empty temporary directory; whitespace clean.
29. **frontend:** `node tests/browser/browsePaging.check.mjs`
    — exit 0; final retained browser harness passed all four groups, including
    added heading-count assertions.
30. **frontend:** `./node_modules/.bin/tsc -b && node --experimental-strip-types --import ./tests/unit/registerTypeScriptResolution.mjs --test tests/unit/browsePaging.test.ts tests/unit/entityListQueryOptions.test.ts`
    — exit 0; final TypeScript check and 7 focused tests passed.
31. `git diff --check && git diff --numstat && git diff --exit-code -- tests/unit/test_2720_browse_paging.py scripts/autopilot-tick.sh scripts/lib scripts/preflight.sh tests/autopilot && git status --short`
    — exit 0; reviewed size/status, whitespace clean, protected paths unchanged.

Final staging, commit and post-commit inspection commands/results are reported
in the delivery message, so this file does not have to amend its own commit.

**Update before review:** the standalone `frontend/tests/browser` check was removed because no script or CI job runs it. The manager instead drove the real app on a private rig built from this branch with 629 authors: 200 rows on open, scrolling fetched offsets 200, 400 and 600 (629 shown), the name filter sent `q` and showed "Showing 10 of 629 authors.", Series paged and filtered, and the flat Tags list still loaded in full from `/api/v1/tags`.
