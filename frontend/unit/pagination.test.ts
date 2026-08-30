import assert from 'node:assert/strict';
import test from 'node:test';

import { clampOffset, clampPage, lastValidOffset } from '../src/lib/pagination.ts';

test('R3 F3: removing the last item on a last page clamps every device pagination shape', () => {
  assert.equal(lastValidOffset(101, 100), 100);
  assert.equal(lastValidOffset(100, 100), 0);
  assert.equal(clampOffset(100, 100, 100), 0);
  assert.equal(clampOffset(200, 199, 200), 0);
  assert.equal(clampOffset(200, 201, 200), 200);
  assert.equal(clampOffset(100, 0, 100), 0);
  assert.equal(clampPage(3, 2), 2);
  assert.equal(clampPage(10_001, 10_000), 10_000);
  assert.equal(clampPage(2, 0), 1);
});
