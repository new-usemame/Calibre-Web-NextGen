import assert from 'node:assert/strict';
import test from 'node:test';

import { applySelectionClick } from '../src/lib/rangeSelection.ts';

const ids = [10, 11, 12, 13, 14, 15];
const click = (selected: number[], id: number, anchor: number | null, extend: boolean) => {
  const result = applySelectionClick(new Set(selected), ids, id, anchor, extend);
  return { selected: [...result.selected].sort((a, b) => a - b), anchor: result.anchor };
};

test('a plain click toggles only the clicked book and becomes the anchor', () => {
  assert.deepEqual(click([], 12, null, false), { selected: [12], anchor: 12 });
  assert.deepEqual(click([12, 14], 12, 14, false), { selected: [14], anchor: 12 });
});

test('shift-click selects every book between the anchor and the clicked one, in either direction', () => {
  assert.deepEqual(click([11], 14, 11, true), { selected: [11, 12, 13, 14], anchor: 14 });
  assert.deepEqual(click([14], 11, 14, true), { selected: [11, 12, 13, 14], anchor: 11 });
});

test('shift-click keeps selections outside the range', () => {
  assert.deepEqual(click([10, 13, 15], 13, 13, false).selected, [10, 15]);
  assert.deepEqual(click([10, 12], 14, 12, true).selected, [10, 12, 13, 14]);
});

test('shift-click onto a selected book deselects the whole range', () => {
  // The clicked book's new state is applied to the range, as in a mail list.
  assert.deepEqual(click([10, 11, 12, 13, 14], 13, 11, true), { selected: [10, 14], anchor: 13 });
});

test('shift-click without a usable anchor behaves as a plain click', () => {
  assert.deepEqual(click([], 13, null, true), { selected: [13], anchor: 13 });
  // The anchor book left the list (a bulk action refreshed it).
  assert.deepEqual(click([], 13, 99, true), { selected: [13], anchor: 13 });
});
