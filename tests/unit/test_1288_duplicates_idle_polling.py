"""#1288 (CWA upstream) — the duplicates badge must not poll a quiet instance.

`duplicate-notifier.js` hammered ``GET /duplicates/status`` every 2.5s for as
long as a tab stayed open, on an instance where nothing was happening. The
reporter saw it as reverse-proxy log bloat; it is also needless server work on
every page of every session.

Root cause is NOT the interval, it is the restart condition. ``handleStatusResponse``
ended with an unconditional ``if (data.enabled) startStatusPolling()``, so every
response re-armed the timer, and the response to the 60th (final) attempt landed
*after* ``stopStatusPolling()`` had nulled ``pollTimer`` — re-arming a fresh
60-attempt cycle forever. Raising the interval (the shape proposed in fork #1018)
divides the traffic but keeps the loop, and silently stretches the
``POLL_MAX_ATTEMPTS = 60 // ~2.5 minutes`` bound to ~60 minutes.

The fix polls only while a scan is genuinely in flight (``stale`` mirrors the
server's ``scan_pending``), backs off while it runs, and stops on the first
settled response. ``needs_scan`` deliberately does NOT poll: it means "a manual
full scan is required", a user-action state that no amount of polling resolves.

These are behavioural tests — they execute the shipped file in Node against a
stubbed DOM/fetch with a controllable clock and count real requests. A
source-pin on the constant would have passed on the #1018 shape.
"""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

NOTIFIER_JS = (
    Path(__file__).resolve().parents[2]
    / "cps" / "static" / "js" / "duplicate-notifier.js"
)

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")


HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const scenario = JSON.parse(process.argv[3]);

// --- virtual clock: setTimeout/setInterval queue we can advance deterministically
let now = 0;
let seq = 0;
const timers = new Map();
function addTimer(fn, delay, repeating) {
  const id = ++seq;
  timers.set(id, { fn, delay: Math.max(0, delay || 0), at: now + Math.max(0, delay || 0), repeating });
  return id;
}
const clearTimer = (id) => timers.delete(id);
async function advance(ms) {
  const target = now + ms;
  for (let guard = 0; guard < 100000; guard++) {
    let next = null;
    for (const [id, t] of timers) if (t.at <= target && (next === null || t.at < timers.get(next).at)) next = id;
    if (next === null) break;
    const t = timers.get(next);
    now = t.at;
    if (t.repeating) t.at = now + t.delay; else timers.delete(next);
    t.fn();
    // let the fetch chain (real promises) resolve before the next tick fires
    await settle();
  }
  now = target;
}

// --- real promises; we just need a way to let all pending microtasks settle
const drainMicro = () => {};
const settle = () => new Promise((r) => setImmediate(r));

// --- request log. Real Promise + real Response-shaped object, so the shipped
// `fetch(...).then(r => r.json()).then(handleStatusResponse)` chain runs as written.
const requests = [];
function fetchImpl(url) {
  requests.push({ url, t: now });
  const body = scenario.responses[Math.min(requests.length - 1, scenario.responses.length - 1)];
  return Promise.resolve({ json: () => Promise.resolve(body) });
}

// --- minimal DOM
function makeEl(id) {
  return {
    id, textContent: '', innerHTML: '', style: {},
    classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, contains(c) { return this._s.has(c); } },
    addEventListener() {}, focus() {}, setAttribute() {}, getAttribute() { return null; },
  };
}
const els = {};
for (const id of scenario.elements) els[id] = makeEl(id);

const store = {};
global.sessionStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
};
global.document = {
  readyState: 'complete',
  getElementById: (id) => els[id] || null,
  createElement: () => ({ set textContent(v) { this._t = v; }, get innerHTML() { return this._t; } }),
  addEventListener: (ev, fn) => { (global.__docListeners[ev] = global.__docListeners[ev] || []).push(fn); },
  dispatchEvent: () => true,
  hidden: false,
};
global.__docListeners = {};
global.CustomEvent = function (name, init) { return { name, detail: init && init.detail }; };
global.window = global;
// not the /duplicates page — that page suppresses the modal entirely
global.location = { pathname: scenario.pathname || '/' };
global.setTimeout = (fn, d) => addTimer(fn, d, false);
global.clearTimeout = clearTimer;
global.setInterval = (fn, d) => addTimer(fn, d, true);
global.clearInterval = clearTimer;
global.fetch = fetchImpl;
global.console = { error() {}, warn() {}, log() {} };
global.cwaDuplicateBootstrap = scenario.bootstrap || undefined;

// `eval` is the point of this harness: it executes the SHIPPED file verbatim in a
// stubbed DOM so the assertions below are about real behaviour. The input is our own
// repo file passed by path from the test, never anything user- or network-supplied.
eval(src);

(async () => {
  await settle();                       // the page-load fetch resolves first
  await advance(scenario.advance_ms || 0);
  await settle();
  // process.stdout directly — the shipped file's console is stubbed out above.
  process.stdout.write(JSON.stringify({ requests, count: requests.length }) + "\n");
})();
"""

ELEMENTS = ["duplicate-notification-modal", "duplicate-count-badge"]


def _run(scenario):
    harness = Path(__file__).parent / "_tmp_1288_harness.js"
    harness.write_text(HARNESS, encoding="utf-8")
    try:
        out = subprocess.run(
            [NODE, str(harness), str(NOTIFIER_JS), json.dumps(scenario)],
            capture_output=True, text=True, timeout=60,
        )
        assert out.returncode == 0, f"harness failed:\n{out.stderr}"
        return json.loads(out.stdout.strip().splitlines()[-1])
    finally:
        harness.unlink(missing_ok=True)


def _settled(count=0, enabled=True):
    """A quiet instance: feature on, cache warm, no scan pending, nothing to do."""
    return {
        "success": True, "enabled": enabled, "count": count, "preview": [],
        "cached": True, "stale": False, "needs_scan": False, "needs_full_scan": False,
    }


def _scanning():
    """A scan is in flight — the server reports scan_pending as `stale`."""
    return {
        "success": True, "enabled": True, "count": 0, "preview": [],
        "cached": True, "stale": True, "needs_scan": False, "needs_full_scan": False,
    }


def _needs_scan():
    """A manual full scan is required — user action, not something polling resolves."""
    return {
        "success": True, "enabled": True, "count": 0, "preview": [],
        "cached": False, "stale": True, "needs_scan": True, "needs_full_scan": True,
    }


def test_idle_instance_issues_no_repeat_requests():
    """THE REPORTED BUG. A quiet instance must go silent after the page-load fetch."""
    r = _run({"elements": ELEMENTS, "responses": [_settled()], "advance_ms": 10 * 60 * 1000})
    assert r["count"] == 1, (
        f"idle instance made {r['count']} requests to /duplicates/status in 10 minutes "
        "(expected exactly the one page-load fetch)"
    )


def test_idle_polling_does_not_resume_after_the_attempt_cap():
    """The old loop re-armed itself forever; an hour of idling must stay at one request."""
    r = _run({"elements": ELEMENTS, "responses": [_settled()], "advance_ms": 60 * 60 * 1000})
    assert r["count"] == 1, f"{r['count']} requests in an hour of idling"


def test_idle_with_existing_duplicates_still_does_not_poll():
    """A library that simply has duplicates is a settled state, not a reason to poll."""
    r = _run({"elements": ELEMENTS, "responses": [_settled(count=7)], "advance_ms": 10 * 60 * 1000})
    assert r["count"] == 1, f"{r['count']} requests with a settled non-zero count"


def test_scan_in_flight_is_still_polled():
    """The fix must not trade the bug for a dead badge: a running scan is observed."""
    r = _run({"elements": ELEMENTS, "responses": [_scanning()], "advance_ms": 60 * 1000})
    assert r["count"] >= 4, (
        f"only {r['count']} requests in the first minute of a running scan — "
        "the badge would not update until reload"
    )


def test_polling_stops_on_the_first_settled_response():
    """When the scan finishes, polling ends — it does not keep running at the new interval."""
    scenario = {
        "elements": ELEMENTS,
        "responses": [_scanning(), _scanning(), _settled(count=3)],
        "advance_ms": 10 * 60 * 1000,
    }
    r = _run(scenario)
    assert r["count"] == 3, f"expected 3 requests (2 scanning + 1 settled), got {r['count']}"


def test_running_scan_backs_off_rather_than_hammering():
    """A long scan must not sustain a 2.5s beat for its whole duration."""
    r = _run({"elements": ELEMENTS, "responses": [_scanning()], "advance_ms": 10 * 60 * 1000})
    naive = (10 * 60 * 1000) // 2500
    assert r["count"] < naive / 3, (
        f"{r['count']} requests during a 10-minute scan — no meaningful backoff "
        f"(a flat 2.5s beat would be ~{naive})"
    )


def test_needs_scan_state_does_not_poll():
    """`needs_scan` is 'the admin must trigger a full scan' — polling cannot change it."""
    r = _run({"elements": ELEMENTS, "responses": [_needs_scan()], "advance_ms": 10 * 60 * 1000})
    assert r["count"] <= 2, (
        f"{r['count']} requests while parked in needs_scan — this state is resolved by "
        "an admin action, not by polling"
    )


def test_no_permission_means_no_request_at_all():
    """Without the modal in the DOM the user lacks the permission; stay off the wire."""
    r = _run({"elements": ["duplicate-count-badge"], "responses": [_settled()], "advance_ms": 60 * 1000})
    assert r["count"] == 0, f"{r['count']} requests for a user without duplicates permission"


# --- defects caught by the cross-family review of the first cut of this fix -------
#
# The first version made the poll decision at the END of handleStatusResponse, after
# the `if (isModalActive()) return;` guard. Each of these is a way the polling chain
# could be dropped mid-scan and never restart, which the old free-running setInterval
# did not suffer from. All three are about NOT trading the reported bug for a badge
# that silently stops updating.


def test_modal_open_does_not_drop_the_chain_mid_scan():
    """A scan running while the notification modal is up must still be observed.

    The old setInterval kept ticking through the modal. Returning early before the
    scheduling decision meant one response during an open modal killed polling for
    the life of the tab.
    """
    modal_then_scan = dict(_settled(count=4))  # count>0 + enabled => modal opens
    modal_then_scan["stale"] = True
    r = _run({
        "elements": ELEMENTS,
        "responses": [modal_then_scan],
        "advance_ms": 60 * 1000,
    })
    assert r["count"] >= 4, (
        f"only {r['count']} requests in a minute of scanning with the modal open — "
        "the chain was dropped and the scan result will never land"
    )


def test_failed_response_does_not_poison_the_next_scan():
    """A transient error must reset the budget, not leave it half-spent.

    `pollAttempts` only reset in stopStatusPolling(); an error path that returned
    early left the counter high, so a later scan could cap after a single request.
    """
    failure = {"success": False, "count": 0, "preview": [], "enabled": True}
    # 30 stale polls, then a failure, then a fresh scan for the rest of the run.
    responses = [_scanning()] * 30 + [failure] + [_scanning()] * 400
    r = _run({"elements": ELEMENTS, "responses": responses, "advance_ms": 30 * 60 * 1000})
    # After the failure the episode restarts from a clean counter, so the run as a
    # whole must keep polling well past where a poisoned counter would have stalled.
    assert r["count"] > 32, (
        f"polling stalled at {r['count']} requests after a transient failure — "
        "the attempt budget was not reset"
    )


def test_a_failing_endpoint_does_not_start_polling_a_quiet_instance():
    """The retry path above must not become a new way to hammer an idle server.

    If we were never following a scan, a failing /duplicates/status must leave the
    instance quiet rather than opening a retry loop — that would reintroduce #1288
    for anyone whose endpoint errors (misconfigured proxy, feature mid-migration).
    """
    failure = {"success": False, "count": 0, "preview": [], "enabled": True}
    r = _run({"elements": ELEMENTS, "responses": [failure], "advance_ms": 60 * 60 * 1000})
    assert r["count"] == 1, (
        f"{r['count']} requests in an hour against a failing endpoint on an instance "
        "that was never following a scan"
    )


def test_cap_is_a_bounded_episode_not_a_permanent_stop():
    """Hitting the safety cap must clear state so a later refresh polls again."""
    r = _run({
        "elements": ELEMENTS,
        "responses": [_scanning()],
        "advance_ms": 6 * 60 * 60 * 1000,   # six hours of a wedged scan_pending
    })
    # Bounded: nowhere near a sustained beat over six hours...
    assert r["count"] < 200, f"{r['count']} requests over six hours — cap not enforced"
    # ...but the cap must not be a one-shot budget that never refills.
    assert r["count"] >= 60, f"only {r['count']} requests — episodes never resumed"


def test_attempt_cap_comment_matches_the_interval():
    """#1018 raised the interval and left `// ~2.5 minutes` describing ~60 minutes."""
    import re
    src = NOTIFIER_JS.read_text(encoding="utf-8")
    m = re.search(r"POLL_MAX_ATTEMPTS\s*=\s*(\d+);\s*(//.*)?", src)
    assert m, "POLL_MAX_ATTEMPTS not found"
    comment = (m.group(2) or "")
    assert "2.5 minutes" not in comment, (
        "the attempt-cap comment still claims ~2.5 minutes; with backoff that bound is wrong"
    )
