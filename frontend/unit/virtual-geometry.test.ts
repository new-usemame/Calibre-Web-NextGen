import assert from 'node:assert/strict';
import { test } from 'node:test';
import { rowAt, rowOffsets, rowWindow } from '../src/components/virtualGeometry.ts';

test('measured heights survive reordering and define contiguous offsets and pixel windows', () => {
  const heights = new Map([['long-note', 123.5], ['header', 31], ['note', 56.25]]);
  const offsets = rowOffsets(['header', 'long-note', 'unseen', 'note'], heights, 72);
  assert.deepEqual(offsets, [0, 31, 154.5, 226.5, 282.75]);
  assert.equal(rowAt(offsets, 154.49), 1);
  assert.equal(rowAt(offsets, 154.5), 2);
  assert.deepEqual(rowWindow(offsets, 155, 60, 1), { start: 1, end: 4 });
  assert.deepEqual(rowOffsets(['note', 'header'], heights, 72), [0, 56.25, 87.25]);
});
