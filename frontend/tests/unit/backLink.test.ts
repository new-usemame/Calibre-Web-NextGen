import { afterEach, beforeEach, describe, test } from 'node:test';
import assert from 'node:assert/strict';

import {
  __resetOriginForTests,
  backTarget,
  isListOrigin,
  recordOrigin,
} from '../../src/lib/backLink.ts';

type HistoryEntry = { state: unknown; url: string };

class FakeHistory {
  entries: HistoryEntry[] = [{ state: null, url: '/' }];
  index = 0;
  replaceCalls = 0;

  get state(): unknown {
    return this.entries[this.index].state;
  }

  pushState(state: unknown, _unused: string, url: string | URL | null = null): void {
    this.entries.splice(this.index + 1);
    this.entries.push({ state, url: String(url ?? this.entries[this.index].url) });
    this.index += 1;
  }

  replaceState(state: unknown, _unused: string, url: string | URL | null = null): void {
    this.replaceCalls += 1;
    this.entries[this.index] = {
      state,
      url: String(url ?? this.entries[this.index].url),
    };
  }

  back(): void {
    if (this.index > 0) this.index -= 1;
  }

  forward(): void {
    if (this.index < this.entries.length - 1) this.index += 1;
  }
}

const originalHistoryDescriptor = Object.getOwnPropertyDescriptor(globalThis, 'history');
let fakeHistory: FakeHistory;

beforeEach(() => {
  __resetOriginForTests();
  fakeHistory = new FakeHistory();
  Object.defineProperty(globalThis, 'history', {
    configurable: true,
    value: fakeHistory,
    writable: true,
  });
});

afterEach(() => {
  if (originalHistoryDescriptor) {
    Object.defineProperty(globalThis, 'history', originalHistoryDescriptor);
  } else {
    delete (globalThis as { history?: unknown }).history;
  }
});

describe('list-origin classification', () => {
  test('accepts canonical list-shaped routes', () => {
    for (const path of [
      '/', '/authors', '/authors/12', '/series', '/series/4', '/tags', '/tags/8',
      '/publishers', '/publishers/3', '/languages', '/languages/9', '/ratings',
      '/ratings/10', '/formats', '/formats/epub', '/shelves', '/shelf/2', '/magic/6',
      '/hot', '/discover', '/rated', '/favorites', '/archived', '/search', '/table',
      '/duplicates',
    ]) {
      assert.equal(isListOrigin(path), true, path);
    }
  });

  test('rejects every book sub-page and reader route', () => {
    for (const path of [
      '/book/5',
      '/book/5/edit',
      '/book/5/cover',
      '/book/5/annotations',
      '/read/5',
      '/view/5/epub',
    ]) {
      assert.equal(isListOrigin(path), false, path);
    }
  });

  test('rejects auth, account, admin, settings-shaped and unclassified routes', () => {
    for (const path of [
      '/login', '/magic-link', '/account', '/account/devices', '/upload', '/admin',
      '/whats-new', '/about', '/tasks', '/magic', '/magic/6/edit', '/unknown',
    ]) {
      assert.equal(isListOrigin(path), false, path);
    }
  });

  test('rejects dot segments and backslashes in list-route parameters', () => {
    for (const path of [
      '/authors/.', '/authors/..', '/authors/%2e%2e',
      String.raw`/authors/1\child`, '/authors/1%5Cchild',
    ]) {
      assert.equal(isListOrigin(path), false, path);
    }
  });
});

describe('remembered back target', () => {
  test('reading an unstamped cached-book entry stamps the last list exactly once', () => {
    recordOrigin('/authors/12', '');
    fakeHistory.pushState({ wouter: 42 }, '', '/book/5');

    assert.deepEqual(backTarget(), { href: '/authors/12', isOrigin: true });
    assert.deepEqual(fakeHistory.state, { wouter: 42, cwngOrigin: '/authors/12' });
    assert.equal(fakeHistory.replaceCalls, 1);

    recordOrigin('/tags/8', '');
    assert.deepEqual(backTarget(), { href: '/authors/12', isOrigin: true });
    assert.deepEqual(fakeHistory.state, { wouter: 42, cwngOrigin: '/authors/12' });
    assert.equal(fakeHistory.replaceCalls, 1);
  });

  test('keeps a book entry bound to the list that opened it after browser Back', () => {
    recordOrigin('/authors/12', '');
    fakeHistory.pushState({}, '', '/book/5');
    recordOrigin('/book/5', '');
    assert.deepEqual(backTarget(), { href: '/authors/12', isOrigin: true });

    fakeHistory.pushState({}, '', '/tags/8');
    recordOrigin('/tags/8', '');
    fakeHistory.back();
    recordOrigin('/book/5', '');

    assert.deepEqual(backTarget(), { href: '/authors/12', isOrigin: true });
  });

  test('binds a different book entry to the list that opened that book', () => {
    recordOrigin('/authors/12', '');
    fakeHistory.pushState({}, '', '/book/5');
    recordOrigin('/book/5', '');

    fakeHistory.pushState({}, '', '/tags/8');
    recordOrigin('/tags/8', '');
    fakeHistory.pushState({}, '', '/book/6');
    recordOrigin('/book/6', '');

    assert.deepEqual(backTarget(), { href: '/tags/8', isOrigin: true });
  });

  test('falls back when a book is opened after leaving a list for a non-list route', () => {
    recordOrigin('/authors/12', '');
    recordOrigin('/account', '');
    fakeHistory.pushState({}, '', '/book/5');

    assert.deepEqual(backTarget('/book/5'), { href: '/', isOrigin: false });
  });

  test('falls back when navigating directly from one book to another book', () => {
    recordOrigin('/authors/12', '');
    fakeHistory.pushState({}, '', '/book/5');
    assert.deepEqual(backTarget('/book/5'), { href: '/authors/12', isOrigin: true });

    fakeHistory.pushState({}, '', '/book/9');

    assert.deepEqual(backTarget('/book/9'), { href: '/', isOrigin: false });
  });

  test('a deep-linked book stamps null and falls back when no list was recorded', () => {
    fakeHistory.pushState({}, '', '/book/5');
    recordOrigin('/book/5', '');
    assert.deepEqual(backTarget(), { href: '/', isOrigin: false });
    assert.deepEqual(fakeHistory.state, { cwngOrigin: null });
  });

  test('does not re-stamp an existing book entry and preserves other history state', () => {
    recordOrigin('/authors/12', '');
    fakeHistory.pushState({ wouter: 42 }, '', '/book/5');
    backTarget();
    assert.deepEqual(fakeHistory.state, { wouter: 42, cwngOrigin: '/authors/12' });

    fakeHistory.pushState({}, '', '/tags/8');
    recordOrigin('/tags/8', '');
    fakeHistory.back();
    recordOrigin('/book/5', '');

    assert.deepEqual(fakeHistory.state, { wouter: 42, cwngOrigin: '/authors/12' });
  });

  test('uses the Library wording for the bare library root', () => {
    recordOrigin('/', '');
    fakeHistory.pushState({}, '', '/book/5');
    recordOrigin('/book/5', '');
    assert.deepEqual(backTarget(), { href: '/', isOrigin: false });
  });

  test('uses the Back wording for a queried library result set', () => {
    recordOrigin('/', 'q=whale');
    fakeHistory.pushState({}, '', '/book/5');
    recordOrigin('/book/5', '');
    assert.deepEqual(backTarget(), { href: '/?q=whale', isOrigin: true });
  });

  test('uses the Back wording for an entity origin', () => {
    recordOrigin('/authors/12', '');
    fakeHistory.pushState({}, '', '/book/5');
    recordOrigin('/book/5', '');
    assert.deepEqual(backTarget(), { href: '/authors/12', isOrigin: true });
  });

  test('falls back to the library when no origin was recorded', () => {
    assert.deepEqual(backTarget(), { href: '/', isOrigin: false });
  });

  test('same-book detail and reader routes leave the list origin untouched', () => {
    recordOrigin('/authors/12', 'sort=title');
    fakeHistory.pushState({}, '', '/book/5');
    recordOrigin('/book/5', '');
    assert.deepEqual(backTarget(), { href: '/authors/12?sort=title', isOrigin: true });

    for (const route of [
      '/book/5/edit',
      '/book/5/cover',
      '/book/5/annotations',
      '/read/5',
      '/view/5/epub',
      '/book/5',
    ]) {
      recordOrigin(route, '');
    }

    assert.deepEqual(backTarget(), { href: '/authors/12?sort=title', isOrigin: true });
  });

  test('rejects malformed list candidates before stamping a book entry', () => {
    recordOrigin('/app/authors/12', '');
    recordOrigin('https://books.example/app/authors/12', '');
    recordOrigin('/authors/%2e%2e', '');
    recordOrigin(String.raw`/authors/1\child`, '');
    recordOrigin('/authors/1%5Cchild', '');
    fakeHistory.pushState({}, '', '/book/5');
    recordOrigin('/book/5', '');
    assert.deepEqual(backTarget(), { href: '/', isOrigin: false });
  });

  test('rejects invalid origins read from the current history entry', () => {
    for (const origin of [
      '//evil.example',
      'http://evil.example/x',
      '/authors/1/../../x',
      String.raw`/authors/1\child`,
      '/authors/1%5Cchild',
      '/book/9',
    ]) {
      fakeHistory.replaceState({ cwngOrigin: origin }, '');
      assert.deepEqual(backTarget(), { href: '/', isOrigin: false }, origin);
    }
  });

  test('is a no-op and falls back when History is unavailable', () => {
    delete (globalThis as { history?: unknown }).history;
    recordOrigin('/authors/12', '');
    recordOrigin('/book/5', '');
    assert.deepEqual(backTarget(), { href: '/', isOrigin: false });
  });
});
