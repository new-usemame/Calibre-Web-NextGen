import { test } from 'node:test';
import assert from 'node:assert/strict';
import { observeReaderSelections } from '../../cps/static/js/reading/selection-observer.js';

type Callback = (...args: any[]) => void;
function harness() {
  let now = 0, nextId = 0, range: any = null, reads = 0;
  const timers = new Map<number, { at: number; callback: Callback }>();
  const events = new Map<string, Set<Callback>>();
  const listeners = new Set<Callback>();
  const frame = { tagName: 'IFRAME' };
  const node = {};
  const host = {
    document: { hidden: false, activeElement: frame as unknown },
    performance: { now: () => now },
    setTimeout(callback: Callback, delay: number) { const id = ++nextId; timers.set(id, { at: now + delay, callback }); return id; },
    clearTimeout(id: number) { timers.delete(id); },
    addEventListener(name: string, callback: Callback) { if (!events.has(name)) events.set(name, new Set()); events.get(name)!.add(callback); },
    removeEventListener(name: string, callback: Callback) { events.get(name)?.delete(callback); },
  };
  const contents = {
    document: { defaultView: { frameElement: frame } },
    window: { getSelection() { reads++; return { isCollapsed: !range, rangeCount: range ? 1 : 0, getRangeAt: () => range }; } },
    cfiFromRange: (selected: any) => `cfi:${selected.startOffset}:${selected.endOffset}`,
  };
  const emitted: { at: number; cfi: string }[] = [];
  const rendition = {
    getContents: () => [contents],
    on(_name: string, callback: Callback) { listeners.add(callback); },
    off(_name: string, callback: Callback) { listeners.delete(callback); },
    emit(_name: string, cfi: string, content: unknown) { emitted.push({ at: now, cfi }); listeners.forEach(callback => callback(cfi, content)); },
  };
  function select(start = 0) {
    range = { startContainer: node, endContainer: node, startOffset: start, endOffset: start + 6,
      toString: () => 'Native', cloneRange() { return { ...this }; } };
  }
  function advance(to: number) {
    for (;;) {
      const next = [...timers].sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
      if (!next || next[1].at > to) break;
      now = next[1].at; timers.delete(next[0]); next[1].callback();
    }
    now = to;
  }
  const stop = observeReaderSelections(rendition, host as unknown as Window);
  return { host, contents, rendition, emitted, select, advance, stop, timers, listeners,
    collapse: () => { range = null; }, reads: () => reads,
    event: (name: string) => events.get(name)?.forEach(callback => callback()) };
}

test('native selection debounce wins without a second popup from the observer', () => {
  const h = harness();
  h.advance(140); h.select();
  h.host.setTimeout(() => h.rendition.emit('selected', 'native-cfi', h.contents), 250);
  h.advance(1000);
  assert.deepEqual(h.emitted, [{ at: 390, cfi: 'native-cfi' }]);
  h.stop();
});

test('blocked frame events still deliver settled selections once and observer lifetime is bounded', () => {
  const h = harness();
  h.select(); h.advance(200); h.select(1); h.advance(550);
  assert.equal(h.emitted.length, 0, 'changing drag must settle');
  h.advance(1000);
  assert.deepEqual(h.emitted, [{ at: 600, cfi: 'cfi:1:7' }]);
  // Moving focus to the popup and back must not reopen the same selection.
  h.host.document.activeElement = null; h.advance(1300);
  h.host.document.activeElement = h.contents.document.defaultView.frameElement;
  h.advance(1600); assert.equal(h.emitted.length, 1);
  h.collapse(); h.advance(1800); h.select(1); h.advance(2300);
  assert.equal(h.emitted.length, 2, 'a new selection after collapse remains actionable');
  h.host.document.hidden = true;
  const before = h.reads(); h.advance(2600); assert.equal(h.reads(), before);
  h.event('pagehide'); assert.equal(h.timers.size, 0);
  h.event('pageshow'); h.event('pageshow'); assert.equal(h.timers.size, 1);
  const pending = [...h.timers.values()][0].callback;
  h.stop(); pending(); h.advance(4000);
  assert.equal(h.timers.size, 0); assert.equal(h.listeners.size, 0);
  assert.equal(h.emitted.length, 2);
});

test('a new selection of the same passage is actionable even when collapse occurred between polls', () => {
  const h = harness();
  h.select(); h.advance(600);
  assert.equal(h.emitted.length, 1);
  h.host.document.activeElement = null; h.advance(900);
  h.host.document.activeElement = h.contents.document.defaultView.frameElement;
  // A quick second gesture replaces the selection before the next poll.
  h.collapse(); h.select(); h.advance(1500);
  assert.equal(h.emitted.length, 2);
  h.advance(2100); assert.equal(h.emitted.length, 2, 'unchanged selection stays delivered');
  h.stop();
});
