import assert from 'node:assert/strict';
import test from 'node:test';

import {
  canonicalMagicShelfSortAdoption,
  customMagicShelfSortOptions,
} from '../src/lib/magicShelfSort.ts';

test('an outage fallback is adopted without replacing the saved custom sort', () => {
  assert.deepEqual(
    canonicalMagicShelfSortAdoption('cc-12-asc', 'new', false, false),
    { value: 'new', persist: false },
  );
  assert.deepEqual(
    canonicalMagicShelfSortAdoption('cc-12-asc', 'new', false, true),
    { value: 'new', persist: true },
  );
});

test('a response without custom sort options degrades to no custom options', () => {
  assert.deepEqual(customMagicShelfSortOptions(undefined), []);
  assert.deepEqual(
    customMagicShelfSortOptions([{ value: 'cc-12-asc', label: 'Difficulty ↑' }]),
    [{ value: 'cc-12-asc', label: 'Difficulty ↑' }],
  );
});
