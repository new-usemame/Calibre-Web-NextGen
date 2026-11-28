# SPDX-License-Identifier: GPL-3.0-or-later
"""Classic Admin page: feedback while a support pack builds (fork issue #2728).

Behavioural, not source-text: the shipped script is loaded in node and its
download protocol is driven with a fake clock, cookie jar and iframe, through
every outcome a person can hit: started, already being prepared, failed,
timed out. Also pins the template wiring that makes the buttons use it and the
colour fix (the Get Support Pack button was btn-primary, black in the caliBlur
theme, while every other button in its row is btn-default).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "cps" / "static" / "js" / "support_pack_download.js"
ADMIN_HTML = (ROOT / "cps" / "templates" / "admin.html").read_text()

# A fake browser: manual timers, a cookie jar, and an iframe whose "load" and text we control.
HARNESS = r"""
const api = require(process.argv[1]);
const scenario = JSON.parse(process.argv[2]);
let now = 0, nextId = 1, timers = new Map();
const jar = {}; const events = []; const frames = [];
const env = {
  token: 'tok_ABCDEFGH12345678',
  cookies: () => Object.entries(jar).map(([k, v]) => k + '=' + v).join('; '),
  clearCookie: (n) => { delete jar[n]; events.push('cookie-cleared'); },
  setInterval: (fn, ms) => { const id = nextId++; timers.set(id, {fn, ms, at: now + ms, every: true}); return id; },
  clearInterval: (id) => timers.delete(id),
  setTimeout: (fn, ms) => { const id = nextId++; timers.set(id, {fn, ms, at: now + ms, every: false}); return id; },
  clearTimeout: (id) => timers.delete(id),
  openFrame: (src, onLoad) => { const f = {src, onLoad, body: scenario.frameText || ''}; f.text = () => f.body; frames.push(f); return f; },
  removeFrameLater: (f) => events.push('frame-removal-scheduled'),
};
function advance(ms) {
  const target = now + ms;
  for (;;) {
    let next = null;
    for (const [id, t] of timers) if (t.at <= target && (!next || t.at < next.t.at)) next = {id, t};
    if (!next) break;
    now = next.t.at;
    if (next.t.every) next.t.at += next.t.ms; else timers.delete(next.id);
    next.t.fn();
  }
  now = target;
}
const result = {};
api.startPackDownload(env, '/admin/debug/support-pack', {
  onDone: () => events.push('done'),
  onError: (kind, message) => events.push('error:' + kind + ':' + (message || '')),
});
for (const step of scenario.steps) {
  if (step.advance) advance(step.advance);
  if (step.cookie) jar['cwng_pack_token'] = step.cookie === 'TOKEN' ? env.token : step.cookie;
  if (step.load) frames[0].onLoad();
  if (step.frameText !== undefined) frames[0].body = step.frameText;
}
console.log(JSON.stringify({events, src: frames[0].src, timersLeft: timers.size}));
"""


def _run(scenario):
    out = subprocess.run(["node", "-e", HARNESS, str(JS), json.dumps(scenario)],
                         capture_output=True, text=True, check=True).stdout
    result = json.loads(out)
    # What the page is told, apart from housekeeping events such as clearing the cookie.
    result["outcomes"] = [e for e in result["events"] if e == "done" or e.startswith("error:")]
    return result


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@needs_node
class TestProtocol:
    def test_requests_the_download_url_with_the_token(self):
        result = _run({"steps": []})
        assert result["src"] == "/admin/debug/support-pack?dl_token=tok_ABCDEFGH12345678"

    def test_cookie_with_our_token_means_the_download_started(self):
        result = _run({"steps": [{"advance": 600}, {"cookie": "TOKEN"}, {"advance": 300}]})
        assert result["outcomes"] == ["done"]
        # Done means tidy: cookie cleared, frame removal scheduled (never immediate), timers stopped.
        assert "cookie-cleared" in result["events"] and "frame-removal-scheduled" in result["events"]
        assert result["timersLeft"] == 0

    def test_a_cookie_with_someone_elses_token_is_ignored(self):
        result = _run({"steps": [{"cookie": "another_tab_token_99"}, {"advance": 2000}]})
        assert result["outcomes"] == []

    def test_started_is_reported_exactly_once(self):
        result = _run({"steps": [{"cookie": "TOKEN"}, {"advance": 1000}, {"load": True}, {"advance": 2000}]})
        assert result["outcomes"] == ["done"]

    def test_busy_response_is_reported_as_busy_with_the_servers_message(self):
        body = json.dumps({"success": False, "busy": True, "message": "A support pack is already being prepared."})
        result = _run({"frameText": body, "steps": [{"load": True}, {"advance": 500}]})
        assert result["outcomes"] == ["error:busy:A support pack is already being prepared."]

    def test_an_html_error_page_is_a_failure(self):
        result = _run({"frameText": "504 Gateway Time-out nginx", "steps": [{"load": True}, {"advance": 500}]})
        assert result["outcomes"] == ["error:failed:"]

    def test_an_empty_frame_with_no_cookie_is_a_failure(self):
        result = _run({"frameText": "", "steps": [{"load": True}, {"advance": 500}]})
        assert result["outcomes"] == ["error:failed:"]

    def test_some_browsers_fire_load_for_a_download_and_the_cookie_wins(self):
        # load fires, the cookie lands within the grace period: still a success, not a failure.
        result = _run({"frameText": "", "steps": [{"load": True}, {"advance": 100}, {"cookie": "TOKEN"}, {"advance": 500}]})
        assert result["outcomes"] == ["done"]

    def test_gives_up_with_a_timeout_when_nothing_ever_happens(self):
        result = _run({"steps": [{"advance": 149000}]})
        assert result["outcomes"] == []
        result = _run({"steps": [{"advance": 151000}]})
        assert result["outcomes"] == ["error:timeout:"] and result["timersLeft"] == 0

    def test_a_late_cookie_after_giving_up_changes_nothing(self):
        result = _run({"steps": [{"advance": 151000}, {"cookie": "TOKEN"}, {"advance": 1000}]})
        assert result["outcomes"] == ["error:timeout:"]


@needs_node
class TestHelpers:
    def _call(self, expression):
        out = subprocess.run(["node", "-e", f"const a=require(process.argv[1]);console.log(JSON.stringify({expression}))",
                              str(JS)], capture_output=True, text=True, check=True).stdout
        return json.loads(out)

    def test_cookie_parsing(self):
        assert self._call("a.cookieValue('a=1; cwng_pack_token=xyz; b=2','cwng_pack_token')") == "xyz"
        assert self._call("a.cookieValue('xcwng_pack_token=no; b=2','cwng_pack_token')") is None
        assert self._call("a.cookieValue('','cwng_pack_token')") is None

    def test_tokens_are_the_shape_the_server_accepts(self):
        tokens = self._call("[0, 0.5, 0.999999].map(r => a.makeToken(() => r))")
        for token in tokens:
            assert re.fullmatch(r"[A-Za-z0-9_-]{8,64}", token)

    def test_token_is_appended_to_a_url_with_or_without_a_query(self):
        assert self._call("a.withToken('/x','t')") == "/x?dl_token=t"
        assert self._call("a.withToken('/x?a=1','t')") == "/x?a=1&dl_token=t"


class TestTemplateWiring:
    def test_the_support_pack_button_matches_its_neighbours(self):
        pack = re.search(r'<a class="([^"]+)" id="support_pack"', ADMIN_HTML)
        debug = re.search(r'<a class="([^"]+)" id="debug"', ADMIN_HTML)
        logs = re.search(r'<a class="([^"]+)" id="logfile"', ADMIN_HTML)
        assert pack and debug and logs
        assert pack.group(1) == debug.group(1) == logs.group(1) == "btn btn-default"

    def test_both_pack_downloads_opt_in_and_have_a_status_line_with_every_message(self):
        assert len(re.findall(r'id="(?:support_pack|debug)"[^>]*\bdata-pack-download\b', ADMIN_HTML)) == 2
        status = re.search(r'<span id="pack_status"[^>]*role="status"[^>]*>', ADMIN_HTML, re.S)
        assert status and 'aria-live="polite"' in status.group(0)
        for name in ("preparing", "done", "busy", "failed", "timeout"):
            assert f"data-{name}=" in status.group(0), name

    def test_the_status_line_carries_the_mount_prefix_for_the_cookie_path(self):
        status = re.search(r'<span id="pack_status"[^>]*>', ADMIN_HTML, re.S)
        assert status and 'data-mount-prefix="{{ request.script_root }}"' in status.group(0)

    def test_the_script_is_loaded_from_static_not_inline(self):
        assert "{% block js %}" in ADMIN_HTML
        assert "filename='js/support_pack_download.js'" in ADMIN_HTML
        assert JS.exists()


# A fake page: two pack buttons, a status line, a cookie jar, hidden iframes, and manual timers.
DOM_HARNESS = r"""
const api = require(process.argv[1]);
const script = JSON.parse(process.argv[2]);
const opts = JSON.parse(process.argv[3] || '{}');
let now = 0, nextId = 1; const timers = new Map();
function el(attrs) {
  const classes = new Set((attrs.class || '').split(' ').filter(Boolean));
  const listeners = {}; const a = Object.assign({}, attrs);
  return { attrs: a, listeners, style: {}, textContent: '', className: attrs.class || '', parentNode: null,
    classList: { add: c => classes.add(c), remove: c => classes.delete(c), has: c => classes.has(c) },
    getAttribute: n => (n in a ? a[n] : null), setAttribute: (n, v) => { a[n] = String(v); },
    addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
    appendChild(c) { c.parentNode = this; frames.push(c); }, removeChild() {},
    set src(v) { this._src = v; }, get src() { return this._src; } };
}
const frames = [];
const status = el(Object.assign({id: 'pack_status', 'data-preparing': 'PREPARING', 'data-done': 'DONE', 'data-busy': 'BUSY',
  'data-failed': 'FAILED', 'data-timeout': 'TIMEOUT'}, opts.prefix === undefined ? {} : {'data-mount-prefix': opts.prefix}));
const pack = el({id: 'support_pack', class: 'btn btn-default', href: '/admin/debug/support-pack'});
const debug = el({id: 'debug', class: 'btn btn-default', href: '/admin/debug'});
const jar = {};   // name -> {val, path}: a cookie is only removed by a write with its own Path, as in a browser
const doc = {
  getElementById: id => (id === 'pack_status' ? status : null),
  querySelectorAll: () => [pack, debug],
  createElement: () => Object.assign(el({}), {contentDocument: {body: {textContent: ''}}}),
  body: el({}),
  get cookie() { return Object.entries(jar).map(([k, e]) => k + '=' + e.val).join('; '); },
  set cookie(v) { const [pair, ...rest] = v.split(';'); const [k, val] = pair.split('=');
    const attr = rest.map(x => x.trim()).find(x => /^path=/i.test(x));
    const path = attr ? attr.slice(5) : '/';
    if (rest.some(x => /Max-Age=0/i.test(x))) { if (jar[k] && jar[k].path === path) delete jar[k]; }
    else jar[k] = {val, path}; },
};
const win = {
  setTimeout: (fn, ms) => { const id = nextId++; timers.set(id, {fn, ms, at: now + ms, every: false}); return id; },
  setInterval: (fn, ms) => { const id = nextId++; timers.set(id, {fn, ms, at: now + ms, every: true}); return id; },
  clearTimeout: id => timers.delete(id), clearInterval: id => timers.delete(id),
};
function advance(ms) {
  const target = now + ms;
  for (;;) {
    let next = null;
    for (const [id, t] of timers) if (t.at <= target && (!next || t.at < next.t.at)) next = {id, t};
    if (!next) break;
    now = next.t.at; if (next.t.every) next.t.at += next.t.ms; else timers.delete(next.id); next.t.fn();
  }
  now = target;
}
api.init(doc, win);
const targets = {pack, debug};
const snap = () => ({status: status.textContent, level: status.className,
  packDisabled: pack.classList.has('disabled'), debugDisabled: debug.classList.has('disabled'),
  ariaDisabled: pack.getAttribute('aria-disabled'), frames: frames.length});
const log = [];
for (const step of script) {
  if (step.click) {
    const prevented = {v: false};
    const ev = Object.assign({defaultPrevented: false, button: 0, metaKey: false, ctrlKey: false, shiftKey: false,
      altKey: false, preventDefault() { prevented.v = true; }}, step.event || {});
    targets[step.click].listeners.click[0](ev);
    log.push({after: 'click ' + step.click, prevented: prevented.v, ...snap()});
  }
  if (step.advance) advance(step.advance);
  if (step.cookie) { const f = frames[frames.length - 1]; const token = f.src.split('dl_token=')[1];
    jar['cwng_pack_token'] = {val: token, path: opts.serverPath || '/'}; }
  if (step.jar) log.push({after: 'jar', jar: Object.keys(jar)});
  if (step.frameLoad) { const f = frames[frames.length - 1]; f.contentDocument.body.textContent = step.frameLoad.text;
    f.listeners.load[0](); }
  if (step.snap) log.push({after: step.snap, ...snap()});
}
console.log(JSON.stringify(log));
"""


def _dom(script, **opts):
    out = subprocess.run(["node", "-e", DOM_HARNESS, str(JS), json.dumps(script), json.dumps(opts)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@needs_node
class TestButtons:
    def test_click_shows_preparing_locks_both_buttons_and_cancels_the_plain_navigation(self):
        (after,) = _dom([{"click": "pack"}])
        assert after["prevented"] is True and after["status"] == "PREPARING" and "text-info" in after["level"]
        assert after["packDisabled"] and after["debugDisabled"] and after["ariaDisabled"] == "true"
        assert after["frames"] == 1

    def test_a_second_click_while_building_does_nothing(self):
        log = _dom([{"click": "pack"}, {"click": "pack"}, {"click": "debug"}])
        assert [entry["frames"] for entry in log] == [1, 1, 1]       # no extra request
        assert all(entry["prevented"] for entry in log)               # and no stray navigation

    def test_when_the_download_starts_buttons_come_back_and_it_says_so_then_clears(self):
        log = _dom([{"click": "pack"}, {"advance": 700}, {"cookie": True}, {"advance": 300, "snap": "started"},
                    {"advance": 9000, "snap": "later"}])
        started, later = log[1], log[2]
        assert started["status"] == "DONE" and "text-success" in started["level"]
        assert not started["packDisabled"] and not started["debugDisabled"] and started["ariaDisabled"] == "false"
        assert later["status"] == ""

    def test_busy_is_shown_in_the_translated_message_not_the_servers_english(self):
        body = json.dumps({"success": False, "busy": True, "message": "English server text"})
        log = _dom([{"click": "pack"}, {"frameLoad": {"text": body}}, {"advance": 600, "snap": "busy"}])
        busy = log[1]
        assert busy["status"] == "BUSY" and "text-danger" in busy["level"] and not busy["packDisabled"]

    def test_failure_and_timeout_show_their_messages_and_unlock(self):
        failed = _dom([{"click": "debug"}, {"frameLoad": {"text": "504 Gateway Time-out"}}, {"advance": 600, "snap": "x"}])[1]
        assert failed["status"] == "FAILED" and not failed["debugDisabled"]
        timed_out = _dom([{"click": "pack"}, {"advance": 151000, "snap": "x"}])[1]
        assert timed_out["status"] == "TIMEOUT" and not timed_out["packDisabled"]

    def test_you_can_try_again_after_a_failure(self):
        log = _dom([{"click": "pack"}, {"frameLoad": {"text": "boom"}}, {"advance": 600}, {"click": "pack"}])
        assert log[-1]["frames"] == 2 and log[-1]["status"] == "PREPARING"

    @pytest.mark.parametrize("event", [{"metaKey": True}, {"ctrlKey": True}, {"shiftKey": True}, {"altKey": True},
                                       {"button": 1}, {"defaultPrevented": True}])
    def test_modified_clicks_keep_their_normal_behaviour(self, event):
        (after,) = _dom([{"click": "pack", "event": event}])
        assert after["prevented"] is False and after["frames"] == 0 and after["status"] == ""


@needs_node
class TestCookiePath:
    """The server scopes the download-started cookie to the app's mount path; the
    page has to clear it with the same Path or the browser keeps it."""

    FLOW = [{"click": "pack"}, {"advance": 700}, {"cookie": True}, {"advance": 300}, {"jar": True}]

    @pytest.mark.parametrize("prefix, server_path", [("", "/"), ("/calibre", "/calibre"), ("/books/cwng", "/books/cwng")])
    def test_the_cookie_is_cleared_at_the_path_the_server_set_it_on(self, prefix, server_path):
        log = _dom(self.FLOW, prefix=prefix, serverPath=server_path)
        assert log[-1]["jar"] == []

    def test_clearing_at_the_wrong_path_would_leave_it_behind(self):
        # Guards the harness itself: it must be able to tell a path mismatch from a clean-up.
        log = _dom(self.FLOW, prefix="", serverPath="/calibre")  # page thinks it is at the root
        assert log[-1]["jar"] == ["cwng_pack_token"]

    @pytest.mark.parametrize("prefix, expected", [
        ("", "/"), ("/calibre", "/calibre"), ("/books/cwng", "/books/cwng"), ("/calibre/", "/calibre"),
        ("//evil.example", "/"), ("/a b", "/"), ("/x;path=/", "/"), ("/../up", "/"), ("calibre", "/"),
    ])
    def test_the_path_follows_the_servers_rule_including_the_fallback_to_root(self, prefix, expected):
        # The same table as tests/unit/test_support_pack_download_token.py: the two must agree.
        out = subprocess.run(["node", "-e", "console.log(JSON.stringify(require(process.argv[1]).cookiePathFor(process.argv[2])))",
                              str(JS), prefix], capture_output=True, text=True, check=True).stdout
        assert json.loads(out) == expected

    def test_the_clearing_write_names_the_path(self):
        out = subprocess.run(["node", "-e", "console.log(require(process.argv[1]).clearCookieString('c', '/calibre'))", str(JS)],
                             capture_output=True, text=True, check=True).stdout.strip()
        assert out == "c=; Max-Age=0; path=/calibre"


@needs_node
class TestMutations:
    """The tests above must notice if the protocol is weakened."""

    def _with_source(self, tmp_path, old, new):
        source = JS.read_text()
        assert old in source
        mutated = tmp_path / "mutated.js"
        mutated.write_text(source.replace(old, new, 1))
        return mutated

    def _outcomes(self, script_path, scenario):
        out = subprocess.run(["node", "-e", HARNESS, str(script_path), json.dumps(scenario)],
                             capture_output=True, text=True, check=True).stdout
        return [e for e in json.loads(out)["events"] if e == "done" or e.startswith("error:")]

    def test_accepting_any_token_is_caught(self, tmp_path):
        mutated = self._with_source(tmp_path, "=== env.token;", "!== null;")
        assert self._outcomes(mutated, {"steps": [{"cookie": "another_tab_token_99"}, {"advance": 2000}]}) == ["done"]
        assert self._outcomes(JS, {"steps": [{"cookie": "another_tab_token_99"}, {"advance": 2000}]}) == []

    def test_always_clearing_at_the_root_is_caught(self, tmp_path):
        mutated = self._with_source(tmp_path, "clearCookieString(name, cookiePath)", 'clearCookieString(name, "/")')
        flow = TestCookiePath.FLOW
        for source, expected in ((mutated, ["cwng_pack_token"]), (JS, [])):
            out = subprocess.run(["node", "-e", DOM_HARNESS, str(source), json.dumps(flow),
                                  json.dumps({"prefix": "/calibre", "serverPath": "/calibre"})],
                                 capture_output=True, text=True, check=True).stdout
            assert json.loads(out)[-1]["jar"] == expected

    def test_judging_the_frame_before_the_cookie_can_land_is_caught(self, tmp_path):
        mutated = self._with_source(tmp_path, "}, GRACE_MS);", "}, 0);")
        scenario = {"frameText": "", "steps": [{"load": True}, {"advance": 100}, {"cookie": "TOKEN"}, {"advance": 500}]}
        assert self._outcomes(mutated, scenario) == ["error:failed:"]      # wrongly reports a failure
        assert self._outcomes(JS, scenario) == ["done"]
