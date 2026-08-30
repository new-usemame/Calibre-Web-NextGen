import assert from 'node:assert/strict';
import test from 'node:test';
import { buildGridRowOffsets, calculateGridWindow } from '../src/lib/gridWindow.ts';

test('grid offsets combine observed and estimated row heights', () => {
  const offsets = buildGridRowOffsets(4, new Map([[0, 300], [2, 420]]), 360);
  assert.deepEqual(offsets, [0, 300, 660, 1080, 1440]);
});

test('grid window keeps complete rows plus viewport overscan', () => {
  const offsets = buildGridRowOffsets(20, new Map(), 100);
  const window = calculateGridWindow(offsets, 900, 300, 1);

  assert.deepEqual(window, {
    startRow: 6,
    endRow: 15,
    topSpacer: 600,
    bottomSpacer: 500,
    totalHeight: 2000,
  });
});

test('empty grids and a viewport beyond the estimate stay bounded', () => {
  assert.deepEqual(calculateGridWindow([0], 100, 500), {
    startRow: 0,
    endRow: 0,
    topSpacer: 0,
    bottomSpacer: 0,
    totalHeight: 0,
  });

  const offsets = buildGridRowOffsets(3, new Map(), 100);
  assert.deepEqual(calculateGridWindow(offsets, 10_000, 500, 0), {
    startRow: 2,
    endRow: 3,
    topSpacer: 200,
    bottomSpacer: 0,
    totalHeight: 300,
  });
});
