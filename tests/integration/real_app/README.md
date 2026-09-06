# Real application integration fixture (P0.1)

Status: fixture and lifecycle exercised; Docker parity is claimed for the
backend wire only, and runs only where a matching image exists.

1. **Fixture — OBSERVED.** `conftest.py:11` supplies a real Flask app from
   `create_app(cps.config, services)` followed by `register_blueprints`.
   Storage uses the shipped empty Calibre schema and production settings models.
   No updater, scheduler, CSRF, middleware, service, or blueprint is stubbed.
   Environment paths and Python's cached temporary directory are isolated.
   The real startup cleanup task therefore operates on disposable scratch.
   `cases.py:73` checks configured storage, middleware, CSRF, error handlers,
   and the actual login response. The registration negative control observes
   `/login` changing from 404 without registration to 200 with registration;
   tokenless POST remains 400. The fixture does not disable CSRF to make it pass.

   The outer integration test starts a fresh pytest interpreter to avoid the
   existing unit collection's process-global stubs. Inside it, tests receive
   the actual app, not an HTTP proxy or serialized substitute. Add further
   real-app cases to `cases.py`, requesting `real_app` and using
   `real_app.test_client()`. The child has five tests; the outer process has
   two tests. `cases.py` deliberately does not match automatic discovery.

2. **Blueprint requests — OBSERVED; parity — UNVERIFIED.** `cases.py:88`
   derives probes from `app.blueprints` and `app.url_map`. It selects a
   reachable rule, accounting for rules shadowed by earlier blueprints.
   Observed: 34 registered blueprints, 33 HTTP probes — with the shipped defaults. Four more (`kobo`, `kobo_auth`, `readingservices_api_v3`, `readingservices_userstorage`) register only when `kobo_available` holds (`cps/main.py:124`) and are outside this fixture's reach; `blueprints.json` lists them so the gap is visible rather than silent. `jinjia` has no routes;
   its real template filter is exercised instead. An unknown routeless
   blueprint fails explicitly. OAuth probes use credential-free callbacks,
   without following provider redirects. These are anonymous smoke requests,
   not authenticated feature coverage.

   The Docker comparator checks status, MIME type, Location, and JSON body
   against those same requests. It requires a usable daemon and matching
   actual backend source in `CWA_TEST_IMAGE`. It owns a unique disposable
   container, anonymous volumes, and an ephemeral port. It does not use the
   shared lane container name, pull images, or write a Compose override.
   An explicitly selected stale image fails; a stale default dev image skips.

   Against the default `crocodilestick/calibre-web-automated:dev` image an
   actual comparison failed: Docker's `/login/github/authorized` returned
   Location `/login/github`; the fixture returned `/login?local=1`. That image
   is UPSTREAM, not this fork, so the difference is a fork divergence and not a
   product bug — the `revision` label on it (`6eefa9815a96`) belongs to the
   LinuxServer base image and resolves to nothing in this history; an earlier
   draft of this file read it as a CWNG commit. The source checksum
   precondition rejects that mismatched comparison, which is the precondition
   doing real work rather than papering over a divergence.

   OBSERVED by the judge, driving the comparison directly with the
   source-checksum precondition BYPASSED, against a fork-built image 28 of 266
   in-scope files drifted from this checkout: **0 mismatching rows of 31**, ready
   in 4.6 s. That is the first time the comparison has run to completion at all.
   **The comparison as shipped, with the precondition satisfied by a CI-built
   image, has still never run.**

   **Scope, and why it is drawn here.** Two things the comparison must not
   report as parity failures, because neither is the application:

   *The compiled SPA bundle.* `cps/static/app/` is gitignored, and the
   Integration Tests lane builds the image from the checkout without building it
   first (`.github/workflows/tests.yml`, "Build Docker image" then "Run Docker
   integration tests"), so a CI checkout has no bundle while the image it built
   does. OBSERVED in a fresh checkout: `/app/` answers 404, one probe row,
   blueprint `spa`. Asserting the bundle exists would have failed that lane on
   every run.

   *A route that reads the client's source address.* Werkzeug's test client
   presents `REMOTE_ADDR` 127.0.0.1; a request through a container's published
   port arrives from the bridge gateway. OBSERVED, exactly one route differs for
   this reason: `/cwa-internal/duplicate-scan-status` returns 200 to loopback and
   500 to anyone else (`cps/cwa_functions.py:478-493` aborts 403 and the bare
   `except Exception` re-emits it as a 500 — a product defect recorded separately,
   not fixed here). `cases.py` asks every route directly, from a documentation
   -range address (RFC 5737), so this exclusion set is MEASURED on each run rather
   than maintained by hand: a route that stops caring rejoins the comparison, and
   one that starts caring leaves it, without anyone editing a list.

   `_backend_parity_rows` returns the rows it compares and, for each row it does
   not, the reason. A bundle-served exclusion must belong to a blueprint the
   module claims; every other exclusion must carry the measured flag. That
   decision is pinned by `tests/unit/test_real_app_parity_partition.py` (9 cases,
   each seen red against a mutant of the rule).

   **What is still unverified, plainly.** The comparison has never been observed
   green. It skips on a developer machine without a matching image. The one
   environment that builds a matching image is the Integration Tests lane, and
   there `CWA_TEST_IMAGE` is set (`tests.yml:369`), which turns the digest
   mismatch from a skip into a hard failure — so that lane, not a developer, is
   where this leg first proves or disproves itself. A change under
   `tests/integration/` sets `build: true` in `scripts/ci_path_classification.py`,
   which makes that lane gating rather than advisory. The remaining risk is not
   provisioning: it is that a matching image may expose divergences this
   28-file-drifted comparison could not, since the digest covers first-party
   source only and not the dependency graph, the Python version, or runtime
   config.

   Separately, exploratory `/login/generic` returned 500 with default settings;
   no product change or claim of Docker equivalence was made for that route.

3. **Lifecycle — OBSERVED.** `cases.py:7` executes the second factory call
   before other cases create additional apps. Profile instrumentation observes
   the real factory body owning the real native RLock. Foreign-thread probes
   observe refusal while held and successful acquisition after return.
   The updater and scheduler retain their original live thread objects.
   `test_native_lock_stalls_greenlets` measures contention under unpatched
   gevent without invoking the factory from a request or a foreign thread.
   Real output from the three final repetitions:

   ```text
   LIFECYCLE before: updater_alive=False scheduler_exists=False initialized=False
   LIFECYCLE second: updater_alive=True updater_count=1 scheduler_running=True scheduler_thread_count=1 same_runtime=True factory_lock_owned=True foreign_acquire_held=False foreign_acquire_after=True
   LOCK native unpatched: lock_wait -> os_release -> lock_acquired -> greenlet_ran
   LIFECYCLE teardown: updater_alive=False scheduler_running=False
   ```

   This characterizes a blocking constraint; it does not make concurrent or
   request-time factory calls safe. No product lock change was attempted.

4. **Lane and repetitions — OBSERVED locally, not a Linux CI execution.**
   Existing `integration-tests` runs `pytest tests/docker/ tests/integration/`.
   The new outer module declares only `integration`. No workflow edit is
   needed for discovery. Parity is not finished by provisioning: see the
   scope note above for what actually stands in the way. No CI/workflow file
   was edited.

   Commands below abbreviate the mandated absolute interpreter as `$PY`;
   every recorded run used Python 3.12.7 / pytest 9.0.3 from that interpreter.

   ```text
   $PY -m pytest tests/integration/test_real_app.py -m 'smoke or unit' --collect-only -q
   collected 2 items / 2 deselected / 0 selected

   $PY -m pytest -m 'smoke or unit' --collect-only -q
   8813/8936 tests collected (123 deselected) in 6.32s

   $PY -m pytest tests/integration/test_real_app.py tests/unit/test_application_factory.py -q -s -rs --tb=short
   Run 1: child 5 passed in 3.09s; outer 18 passed, 1 skipped in 7.36s
   Run 2: child 5 passed in 3.22s; outer 18 passed, 1 skipped in 7.58s
   Run 3: child 5 passed in 6.02s; outer 18 passed, 1 skipped in 12.48s
   ```

   The first two final skips were the stale Docker image; the third was an
   unavailable daemon. Earlier exploratory failures were not counted as
   successful repetitions. Full fast-lane execution on Linux is unverified.

Changed-file reconciliation against `origin/main`:

- `tests/integration/real_app/conftest.py`: real-app fixture and teardown.
- `tests/integration/real_app/cases.py`: five isolated runtime cases.
- `tests/integration/real_app/blueprints.json`: the pinned blueprint list, split
  into unconditional and conditional. The only artifact here that does not come
  from the app itself, and the only thing that can notice a blueprint vanishing.
- `tests/integration/test_real_app.py`: lane entry, isolation, Docker comparison.
- `tests/unit/test_real_app_parity_partition.py`: the parity exclusion rule,
  which the Docker comparison would otherwise never exercise on a machine
  without a matching image.
- `tests/integration/real_app/README.md`: usage, evidence, outstanding parity.
