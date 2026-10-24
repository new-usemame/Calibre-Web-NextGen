import assert from 'node:assert/strict';
import test from 'node:test';

import { selectAllState, toggleAll, toggleOne } from '../src/lib/coverFlagSelection.ts';

const ids = [4, 8, 15, 16];

test('select-all state reports none, some and all, and none for an empty list', () => {
  assert.equal(selectAllState(new Set(), ids), 'none');
  assert.equal(selectAllState(new Set([8]), ids), 'some');
  assert.equal(selectAllState(new Set(ids), ids), 'all');
  assert.equal(selectAllState(new Set(), []), 'none');
});

test('select-all ignores ids that are not in the list', () => {
  assert.equal(selectAllState(new Set([99]), ids), 'none');
  assert.equal(selectAllState(new Set([99, 4]), ids), 'some');
});

test('toggleAll selects everything unless everything is already selected', () => {
  assert.deepEqual([...toggleAll(new Set(), ids)].sort((a, b) => a - b), ids);
  assert.deepEqual([...toggleAll(new Set([8]), ids)].sort((a, b) => a - b), ids);
  assert.equal(toggleAll(new Set(ids), ids).size, 0);
});

test('toggleOne flips a single book and never mutates the previous selection', () => {
  const before = new Set([4]);
  const added = toggleOne(before, 8);
  const removed = toggleOne(added, 4);
  assert.deepEqual([...before], [4]);
  assert.deepEqual([...added].sort((a, b) => a - b), [4, 8]);
  assert.deepEqual([...removed], [8]);
});
