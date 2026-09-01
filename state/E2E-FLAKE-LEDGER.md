# End-to-end flake ledger

This is the durable record for observed intermittent E2E failures. A red run is
evidence to explain, not a result to retry away. Add a row on first observation;
update its cause/status when evidence changes, preserving the original symptom.

| Observed | Lane / load | Test | Symptom | Root cause | Status |
|---|---|---|---|---|---|
| 2026-08-31 | PR #2084 review; six Playwright workers | `catalog-layout-watchdog-classifier.spec.ts` — `two isolated bad endpoints do not license their unsampled healthy gap` | Failed once because the assertion read the second endpoint before the browser had recorded it. | The test used a fixed 250 ms delay under load. It was replaced with a bounded condition-wait for both bad spans, followed by the existing healthy-grid condition. | **RESOLVED** |
| 2026-08-31 | Private Docker rig; full 660-test matrix; six workers; Playwright 1.62.1; retries disabled | 59 tests across desktop/mobile/touch projects | 521 passed, 59 failed, 71 skipped, 9 did not run; failures included many 45 s navigation and locator timeouts. The always-on catalog watchdog was 17/17 green, including the previously flaky classifier pin. | **Unknown.** A later two-worker run proved two supervised app-process restarts, but the six-worker app log was not preserved, so attributing this first run to those restarts would be an assumption. | **OPEN** |
| 2026-08-31 | Private Docker rig; full 660-test matrix at the configured CI concurrency of two workers; Playwright 1.62.1; retries disabled | 16 tests in shelf/sidebar/SP1/SP2/iPad surfaces | Requests failed immediately with `ERR_EMPTY_RESPONSE` or `ERR_CONNECTION_REFUSED`. The container stayed up at the same image and bundle identity, but its supervised Calibre-Web longrun booted three times (two mid-run restarts). | The transport failures are caused by those two observed app-process availability gaps. What triggered the longrun exits remains unknown; do not label the incident resolved until that trigger is reproduced and fixed. Six additional deterministic assertion/fixture failures in the same run are not classified as flakes. | **OPEN** |
| 2026-09-01 | GitHub-hosted CI; Integration Tests (Docker); PR #2104 run 33547551222 attempt 1 (no product code in the PR) | `tests/docker/test_container_startup.py::TestDockerContainerStartup::test_web_interface_accessible` (fixture setup) | Fixture POSTed the test login and got HTTP 400; the fixture reported "rejected the test credentials ... regression in the login flow". 119 other integration tests passed in the same job. | The availability gate only checks `GET /` is 200, then `/login` is fetched and its CSRF token regexed; a token-less page yields exactly this 400. Attempt 2 on identical bytes passed, which discriminates race from regression but is not a fix. Tracked as F-34acae: make the fixture wait for a token-bearing `/login` and split the two failure diagnoses. | **OPEN** |

## Triage procedure

1. Preserve the first run’s report, trace, logs, worker count and exact lane;
   record the datum above before rerunning anything.
2. Reproduce the same tree under at least the observed load. Increase worker or
   hostile-load pressure only to test a concrete timing hypothesis; do not fan
   out browsers or point parallel agents at the same service.
3. Identify the condition the test actually needs. Replace fixed sleeps with a
   bounded wait for that condition, retaining a timeout that fails loudly with
   the relevant state attached.
4. Prove the diagnosis by making the old timing fail under that load and the
   condition-wait pass repeatedly on the same bytes. If the product is wrong,
   fix the product; do not weaken the assertion.
5. Update the ledger with root cause and `OPEN`, `MITIGATED`, or `RESOLVED`.
   Never use Playwright retries, a rerun button, or “retry until green” as the
   fix or as the evidence that a flake is resolved.
