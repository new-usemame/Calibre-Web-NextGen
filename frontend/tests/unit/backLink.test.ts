import { beforeEach, describe, test } from 'node:test';
import assert from 'node:assert/strict';

import {
  __resetOriginForTests,
  backTarget,
  isListOrigin,
  recordOrigin,
} from '../../src/lib/backLink.ts';

beforeEach(() => __resetOriginForTests());

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
});

describe('remembered back target', () => {
  test('uses the Library wording for the bare library root', () => {
    recordOrigin('/', '');
    assert.deepEqual(backTarget(), { href: '/', isOrigin: false });
  });

  test('uses the Back wording for a queried library result set', () => {
    recordOrigin('/', 'q=whale');
    assert.deepEqual(backTarget(), { href: '/?q=whale', isOrigin: true });
  });

  test('uses the Back wording for an entity origin', () => {
    recordOrigin('/authors/12', '');
    assert.deepEqual(backTarget(), { href: '/authors/12', isOrigin: true });
  });

  test('falls back to the library when no origin was recorded', () => {
    assert.deepEqual(backTarget(), { href: '/', isOrigin: false });
  });

  test('book and edit navigation leave the list origin untouched', () => {
    recordOrigin('/authors/12', 'sort=title');
    recordOrigin('/book/5', '');
    recordOrigin('/book/5/edit', '');
    recordOrigin('/book/5', '');
    assert.deepEqual(backTarget(), { href: '/authors/12?sort=title', isOrigin: true });
  });

  test('stores only base-relative origins', () => {
    recordOrigin('/app/authors/12', '');
    recordOrigin('https://books.example/app/authors/12', '');
    assert.deepEqual(backTarget(), { href: '/', isOrigin: false });

    recordOrigin('/authors/12', 'q=whale');
    const target = backTarget();
    assert.equal(target.href, '/authors/12?q=whale');
    assert.equal(target.href.startsWith('/app'), false);
    assert.equal(target.href.startsWith('http'), false);
  });
});
