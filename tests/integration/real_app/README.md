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
   Observed: 34 registered blueprints, 33 HTTP probes. `jinjia` has no routes;
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

   The available dev image reported revision `6eefa9815a96`, while this
   worktree started at `370d8344ad`. An actual comparison failed: Docker's
   `/login/github/authorized` returned Location `/login/github`; the fixture
   returned `/login?local=1`. This is not evidence of a current product bug.
   The source checksum precondition now rejects that mismatched comparison.

   **Scope, and why it is drawn here.** `cps/static/app/` is the Vite bundle.
   It is gitignored, and the Integration Tests lane builds the image from the
   checkout without building it first (`.github/workflows/tests.yml`, "Build
   Docker image" then "Run Docker integration tests"), so a CI checkout has no
   bundle while the image it built does. OBSERVED in a fresh checkout: `/app/`
   answers 404, one probe row, blueprint `spa`. Requiring the bundle would
   therefore have reddened that lane on every run while proving nothing about
   the application — the difference is the frontend build, not the backend.
   `_backend_parity_rows` compares every row when the checkout has the bundle
   and otherwise excludes the bundle-served rows, refusing to exclude a
   blueprint outside `BUNDLE_SERVED_BLUEPRINTS` so a future route cannot leave
   the comparison unnoticed. That decision is pinned by
   `tests/unit/test_real_app_parity_partition.py` (4 cases, all four seen red
   against mutants of the rule: never-exclude, always-exclude, guard removed,
   backend rows dropped). The comparison itself still runs nowhere on a
   developer machine without a matching image; the integration lane is the one
   environment that builds one.

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
   needed for discovery. To finish parity, supply the current integration
   image and matching checkout SPA build artifact; workflow provisioning
   changes remain with the operator. No CI/workflow file was edited.

   Commands below abbreviate the mandated absolute interpreter as `$PY`;
   every recorded run used Python 3.12.7 / pytest 9.0.3 from that interpreter.

   ```text
   $PY -m pytest tests/integration/test_real_app.py -m 'smoke or unit' --collect-only -q
   collected 2 items / 2 deselected / 0 selected

   $PY -m pytest -m 'smoke or unit' --collect-only -q
   8806/8929 tests collected (123 deselected) in 16.41s

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
- `tests/integration/test_real_app.py`: lane entry, isolation, Docker comparison.
- `tests/integration/real_app/README.md`: usage, evidence, outstanding parity.
