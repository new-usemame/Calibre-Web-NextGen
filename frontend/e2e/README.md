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

The catalog layout watchdog runs in normal Chromium and WebKit lanes. Besides the parent track/count
invariant, it checks the first virtual row's rendered card coordinates so an engine can never collapse
the row to one internal column behind a healthy parent grid. Its hostile-load matrix is opt-in so the
broad suite stays fast: set `E2E_HOSTILE_LOAD=1` and select one or more
`hostile-{css-slow,script-slow}-{chromium,webkit}` projects. The profiles delay fulfilled stylesheet and
JavaScript responses in opposite orders through `page.route`, so the same settling-window reproduction
works in both engines. CWNG currently uses system fonts and requests no webfont, so this lane makes no
font-delay claim. The watchdog measures grid state transitions directly. Around every synchronous
test-realm DOM, CSSOM, declaration, class/dataset, and CSS Typed OM write, it evaluates each invariant
immediately before and after the outermost browser call. A write may establish measured evidence only when
that invariant's healthy/bad truth flips across the call. The transition gate and the recorder share the
same predicate: resolved tracks must equal the width formula's expected count, and an accepted column value
must be absent or equal that count. Ownership, selector matching, property names, and raw input movement
never license duration. An irrelevant `color` write stays diagnostic; a width/minimum class swap that keeps
seven tracks correct also stays diagnostic; and a write that really flips seven correct tracks to one is
measured without a guessable property allowlist or input-vector comparison. Bad-to-differently-bad writes
update diagnostics without splitting or resetting the episode.

CSSOM `insertRule`, `deleteRule`, `replaceSync`, declaration `setProperty`/`removeProperty`/`cssText`,
direct declaration assignments, selector and stylesheet-state setters, `document.adoptedStyleSheets`,
element attributes/classes/datasets, and CSS Typed OM `set`/`delete`/`clear` all use this truth-flip gate.
No computed-style reads occur until a catalog grid is attached, and nested hooks for one browser write
reuse the outermost measurement instead of forcing duplicate reads. A grid-scoped MutationObserver remains
a diagnostic fallback for cross-realm or browser-internal mutations; ResizeObserver supplies the geometry
change boundary. Matching stylesheet lifecycle and relevant-family FontFaceSet notifications are also
diagnostic because their asynchronous callbacks have no synchronous pre-change endpoint. Selector checks
that throw, opaque/cross-origin sheets, and container-query activation that the browser cannot answer remain
diagnostic rather than guessed causal. Each exactly measured bad-to-healthy transition contributes its
actual duration to a per-invariant total for the whole convergence window, so brief heals do not discard
bad time and genuinely healthy stalls are never charged.

The rAF safety sample is diagnostic only. If it is the only surface that observes an episode, the snapshot
records `durationEvidence: "unmeasured-safety-net"` and zero duration; it never infers what happened between
two samples and therefore cannot create either a scheduler-starvation false red or a sample-spacing false
green. A violation still active at settle fails unconditionally. A CSS animation, media/container-query
reevaluation, cross-realm stylesheet mutation, or other browser-internal recalculation that triggers no
grid insertion/resize or synchronously state-changing DOM/CSSOM/Typed-OM write can therefore heal before
settle as diagnostic-only evidence. Asynchronous stylesheet replacement and font application are included
in that residual when no synchronous state-changing write brackets them. This named gap is intentional: CI
reports what it observed but does not turn coincident or unknowable time into a pass/fail duration.

The `CSSStyleDeclaration`, `style`, `dataset`, and `classList` interception is installed at DOM/CSSOM
prototype boundaries in the Playwright test realm. Named CSS declaration properties have no configurable
per-property descriptors in Chromium or WebKit, so declarations use stable proxies. This placement obtains
the synchronous before/after state pair before asynchronous discovery or observer delivery can coalesce
changes; it has no production consumer or production bundle effect.
The private rig already passes all arguments after the worktree through to Playwright:

```bash
E2E_HOSTILE_LOAD=1 /absolute/path/to/local-dev/private-e2e-rig.sh test /absolute/path/to/worktree \
  --project=hostile-css-slow-chromium --project=hostile-css-slow-webkit \
  --project=hostile-script-slow-chromium --project=hostile-script-slow-webkit
```

### Curated visual regression

`visual-regression-chromium` is an opt-in pixel lane with exactly six
`toHaveScreenshot` assertions. A unit policy pin rejects growth past that curated
set. It complements the catalog watchdog rather than repeating it: the watchdog
owns track-count arithmetic and convergence, while this lane owns visible pixels.

| Snapshot | Why it earns one of the six slots |
|---|---|
| Desktop sign-in | The unauthenticated entry point and provider row can fail before any signed-in test is reachable. |
| Desktop catalog | The primary product canvas covers the top bar, sidebar, toolbar, card rhythm, type and badges together. |
| Mobile catalog with drawer open | One frame captures the highest-risk responsive reflow, overlay, scroll boundary and full navigation stack. |
| Desktop book detail | The densest everyday action surface combines cover, title, metadata, progress, formats and permissions. |
| Mobile book detail | The narrow breakpoint radically restacks that same high-value surface and exposes action wrapping/overflow defects. |
| French advanced search | The most control-dense form is rendered through a 100%-translated locale, catching translated-label wrapping that DOM assertions miss. |

The browser never runs on the host. `private-e2e-rig.sh` builds the requested
worktree into its own app image and random private port, identity-checks the
running image id plus the compiled bundle name/hash/marker, then runs Playwright
inside the exact `mcr.microsoft.com/playwright:v1.62.1-noble` image. The runner
shares only the private app container's network namespace and reaches it as
`http://localhost:8083`; that keeps secure-context browser APIs such as the
Clipboard API available without involving port 8083 on the host. The rig
repeats the identity check after the run. Start, test, and tear down explicitly:

```bash
./local-dev/private-e2e-rig.sh up "$PWD"
E2E_VISUAL_REGRESSION=1 \
  ./local-dev/private-e2e-rig.sh test "$PWD" \
  --project=visual-regression-chromium
./local-dev/private-e2e-rig.sh down "$PWD"
```

Determinism is part of the contract, not a tolerance budget. The project pins a
1440×900 CSS viewport at device scale factor 1 (individual mobile frames pin
390×844), dark media and reduced motion. Its screenshot stylesheet zeroes every
animation/transition and hides the caret; Playwright also disables animations,
hides the caret and permits zero differing pixels. The fixture freezes the page
clock, stubs the authenticated identity, catalog rows, book metadata, search
options and all displayed counts, serves one fixed local SVG for every cover,
and makes shelves/notices/device lists empty. Those are stubbed because private
library contents, relative dates, badges and cover bytes can all change without
a CSS regression. The real `fr` catalog still comes from the identity-asserted
rig image so translated production strings—not test copies—drive the French
layout.

Committed baselines live beside the spec under
`e2e/visual-regression.spec.ts-snapshots/` and must be generated by the Linux rig.
To accept an intentional visual change, review the product diff first and run:

```bash
E2E_VISUAL_REGRESSION=1 \
  ./local-dev/private-e2e-rig.sh test "$PWD" \
  --project=visual-regression-chromium --update-snapshots
```

Never copy a host-generated baseline into that directory. On failure, the list
report names the snapshot and `frontend/e2e/.results/` contains
`*-expected.png`, `*-actual.png`, and `*-diff.png`; inspect the diff first, then
the expected/actual pair. `frontend/e2e/.report/index.html` presents the same
attachments together. A rerun is not a triage step and must not be used to turn
a red snapshot green.

### Playwright 1.62 notes for this harness

The runner and lockfile are pinned to 1.62.1. The relevant 1.62 changes are the
Chromium 151 / Firefox 153 / WebKit 26.5 browser roll; lossless WebP golden
support (this suite deliberately keeps PNG for the clearest three-way diff
workflow); headless clipboard isolation; cancellable actions/assertions and
isolated-retry support (available, but this repository keeps its condition-wait
and never-retry-to-green policy); and 1.62.1 fixes for TypeScript
config-reference resolution plus accessible names/actionable images in
accessibility snapshots. A 1.61.1/1.62.1 A/B showed that the Kobo pairing
failure seen during the bump was not a 1.62 regression: both versions lacked
`navigator.clipboard` on the rig's former non-localhost HTTP origin. Running the
browser in the app container's network namespace restored the secure localhost
origin, and the unchanged Kobo test passed on 1.62.1.

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
surfaces; reverse dependents cannot be discovered by that traversal and must be added as roots or package
prefixes. The whole `cps/api/` blueprint tree is therefore protected explicitly: its registration in
`cps/main.py` points toward the handlers, opposite to the import direction walked by the classifier. The
two-level cutoff only bounds each root's dependency fan-out—it is not what excludes reverse dependents.
At this revision the derived set is 218 of 276 local Python modules (the closure correctly picks
