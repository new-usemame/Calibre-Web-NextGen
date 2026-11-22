import assert from 'node:assert/strict';
import test from 'node:test';

import {
  cookieValue,
  GIVE_UP_MS,
  interpretFrame,
  makeToken,
  PACK_COOKIE,
  startPackDownload,
  withToken,
  type PackEnv,
  type PackErrorKind,
} from '../src/lib/packDownload.ts';

/** A fake browser: manual timers, a cookie jar, and a frame whose load and text we control. */
function fakeBrowser(frameText = '') {
  let now = 0;
  let nextId = 1;
  const timers = new Map<number, { fn: () => void; ms: number; at: number; every: boolean }>();
  const jar: Record<string, string> = {};
  const housekeeping: string[] = [];
  const outcomes: string[] = [];
  const frames: { src: string; onLoad: () => void; body: string }[] = [];
  const token = 'tok_ABCDEFGH12345678';

  const env: PackEnv = {
    token,
    cookies: () => Object.entries(jar).map(([k, v]) => `${k}=${v}`).join('; '),
    clearCookie: (name) => { delete jar[name]; housekeeping.push('cookie-cleared'); },
    setInterval: (fn, ms) => { const id = nextId++; timers.set(id, { fn, ms, at: now + ms, every: true }); return id; },
    clearInterval: (id) => { timers.delete(id as number); },
    setTimeout: (fn, ms) => { const id = nextId++; timers.set(id, { fn, ms, at: now + ms, every: false }); return id; },
    clearTimeout: (id) => { timers.delete(id as number); },
    openFrame: (src, onLoad) => {
      const frame = { src, onLoad, body: frameText };
      frames.push(frame);
      return { text: () => frame.body, remove: () => {} };
    },
    removeFrameLater: () => { housekeeping.push('frame-removal-scheduled'); },
  };

  function advance(ms: number) {
    const target = now + ms;
    for (;;) {
      let next: { id: number; t: { fn: () => void; ms: number; at: number; every: boolean } } | null = null;
      for (const [id, t] of timers) if (t.at <= target && (!next || t.at < next.t.at)) next = { id, t };
      if (!next) break;
      now = next.t.at;
      if (next.t.every) next.t.at += next.t.ms; else timers.delete(next.id);
      next.t.fn();
    }
    now = target;
  }

  const handlers = {
    onDone: () => { outcomes.push('done'); },
    onError: (kind: PackErrorKind, message: string | null) => { outcomes.push(`error:${kind}:${message ?? ''}`); },
  };
  return {
    env, frames, housekeeping, outcomes, handlers, advance,
    start: () => startPackDownload(env, '/admin/debug/support-pack', handlers),
    setCookie: (value = token) => { jar[PACK_COOKIE] = value; },
    timersLeft: () => timers.size,
  };
}

test('requests the normal download URL with the token', () => {
  const b = fakeBrowser();
  b.start();
  assert.equal(b.frames[0].src, '/admin/debug/support-pack?dl_token=tok_ABCDEFGH12345678');
});

test('a cookie carrying our token means the download started, and everything is tidied', () => {
  const b = fakeBrowser();
  b.start();
  b.advance(600);
  b.setCookie();
  b.advance(300);
  assert.deepEqual(b.outcomes, ['done']);
  assert.ok(b.housekeeping.includes('cookie-cleared') && b.housekeeping.includes('frame-removal-scheduled'));
  assert.equal(b.timersLeft(), 0);
});

test("a cookie with someone else's token is ignored", () => {
  const b = fakeBrowser();
  b.start();
  b.setCookie('another_tab_token_99');
  b.advance(2000);
  assert.deepEqual(b.outcomes, []);
});

test('started is reported exactly once, even if the frame also fires load', () => {
  const b = fakeBrowser();
  b.start();
  b.setCookie();
  b.advance(1000);
  b.frames[0].onLoad();
  b.advance(2000);
  assert.deepEqual(b.outcomes, ['done']);
});

test("the server's 429 is reported as busy, with its message", () => {
  const b = fakeBrowser(JSON.stringify({ success: false, busy: true, message: 'A support pack is already being prepared.' }));
  b.start();
  b.frames[0].onLoad();
  b.advance(500);
  assert.deepEqual(b.outcomes, ['error:busy:A support pack is already being prepared.']);
});

test('an HTML error page, or an empty frame with no cookie, is a failure', () => {
  for (const body of ['504 Gateway Time-out nginx', '']) {
    const b = fakeBrowser(body);
    b.start();
    b.frames[0].onLoad();
    b.advance(500);
    assert.deepEqual(b.outcomes, ['error:failed:']);
  }
});

test('if load fires for a download, a cookie landing within the grace period still wins', () => {
  const b = fakeBrowser('');
  b.start();
  b.frames[0].onLoad();
  b.advance(100);
  b.setCookie();
  b.advance(500);
  assert.deepEqual(b.outcomes, ['done']);
});

test('gives up with a timeout when nothing ever happens, and a late cookie changes nothing', () => {
  const quiet = fakeBrowser();
  quiet.start();
  quiet.advance(GIVE_UP_MS - 1000);
  assert.deepEqual(quiet.outcomes, []);

  const b = fakeBrowser();
  b.start();
  b.advance(GIVE_UP_MS + 1000);
  b.setCookie();
  b.advance(1000);
  assert.deepEqual(b.outcomes, ['error:timeout:']);
  assert.equal(b.timersLeft(), 0);
});

test('cancel stops watching without reporting anything (the page went away)', () => {
  const b = fakeBrowser();
  const run = b.start();
  run.cancel();
  b.setCookie();
  b.frames[0].onLoad();
  b.advance(GIVE_UP_MS + 5000);
  assert.deepEqual(b.outcomes, []);
  assert.equal(b.timersLeft(), 0);
  run.cancel(); // safe to repeat
});

test('cookie parsing finds the exact name only', () => {
  assert.equal(cookieValue('a=1; cwng_pack_token=xyz; b=2', 'cwng_pack_token'), 'xyz');
  assert.equal(cookieValue('xcwng_pack_token=no; b=2', 'cwng_pack_token'), null);
  assert.equal(cookieValue('', 'cwng_pack_token'), null);
});

test('tokens are the shape the server accepts (8-64 URL-safe characters)', () => {
  for (const r of [0, 0.5, 0.999999]) assert.match(makeToken(() => r), /^[A-Za-z0-9_-]{8,64}$/);
});

test('the token is appended to a URL with or without a query', () => {
  assert.equal(withToken('/x', 't'), '/x?dl_token=t');
  assert.equal(withToken('/x?a=1', 't'), '/x?a=1&dl_token=t');
});

test('only JSON that says busy is busy; everything else is a failure', () => {
  assert.deepEqual(interpretFrame('{"busy": true, "message": "wait"}'), { kind: 'busy', message: 'wait' });
  assert.deepEqual(interpretFrame('{"busy": true}'), { kind: 'busy', message: null });
  assert.deepEqual(interpretFrame('{"busy": false}'), { kind: 'failed', message: null });
  assert.deepEqual(interpretFrame('<html>504</html>'), { kind: 'failed', message: null });
  assert.deepEqual(interpretFrame('null'), { kind: 'failed', message: null });
});
