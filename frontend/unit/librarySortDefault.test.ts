import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DEFAULT_LIBRARY_SORT,
  LIBRARY_SORT_KEY,
  LIBRARY_SORT_KEY_LEGACY,
  SORT_OPTIONS,
  defaultCatalogSort,
  resolveLibrarySort,
} from '../src/lib/bookSortOptions.ts';

const VALUES = SORT_OPTIONS.map((option) => option.value);

test('Recent is the first option offered and the default the library opens on', () => {
  assert.equal(SORT_OPTIONS[0].value, 'recent');
  assert.equal(DEFAULT_LIBRARY_SORT, 'recent');
});

test('a reader who has never touched the menu gets the default', () => {
  assert.equal(resolveLibrarySort(null, null, VALUES), undefined);
});

// The v1 key was written on every mount, not only when the reader chose — so
// every existing install holds a value whether or not anyone picked one, and
// reading v1 as a choice would mean the new default reached nobody. A value
// that differs from what v1 was seeded with can only have come from the menu.
test('a sort the reader actually picked before this change still wins', () => {
  assert.equal(resolveLibrarySort(null, 'authaz', VALUES), 'authaz');
});

test('the value v1 seeded itself with is not read as a choice', () => {
  assert.equal(resolveLibrarySort(null, 'new', VALUES), undefined);
});

test('a choice made since this change wins over anything left in v1', () => {
  assert.equal(resolveLibrarySort('abc', 'authaz', VALUES), 'abc');
  assert.equal(resolveLibrarySort('new', 'authaz', VALUES), 'new');
});

test('choosing Newest since this change is a real choice and sticks', () => {
  assert.equal(resolveLibrarySort('new', null, VALUES), 'new');
});

test('a value this build no longer offers falls through to the default', () => {
  assert.equal(resolveLibrarySort('hotdesc', null, VALUES), undefined);
  assert.equal(resolveLibrarySort('', '', VALUES), undefined);
  assert.equal(resolveLibrarySort('seriesasc', null, VALUES), undefined);
});

test('the two keys are distinct, so the migration can tell them apart', () => {
  assert.notEqual(LIBRARY_SORT_KEY, LIBRARY_SORT_KEY_LEGACY);
});

// Which listing opens on which order. The whole library is the only view that
// remembers a choice, so it is the only one a per-reader default belongs in.
test('the whole library opens on Recent', () => {
  assert.equal(defaultCatalogSort({ isSeries: false, isPlainLibrary: true }), 'recent');
});

test('a series listing still opens in series order', () => {
  assert.equal(defaultCatalogSort({ isSeries: true, isPlainLibrary: false }), 'seriesasc');
});

test('an author, tag or discovery listing still opens on newest added', () => {
  assert.equal(defaultCatalogSort({ isSeries: false, isPlainLibrary: false }), 'new');
});
