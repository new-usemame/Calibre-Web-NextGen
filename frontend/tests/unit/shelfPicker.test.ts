import { describe, test } from 'node:test';
import assert from 'node:assert/strict';

import { togglePick, mergePickerPages, retainFailed, type PickerBook } from '../../src/lib/shelfPicker.ts';

const book = (id: number, in_shelf = false): PickerBook => ({
  id, title: `Book ${id}`, authors: [], cover_url: null, in_shelf,
});

describe('togglePick', () => {
  test('adds and removes a book without mutating the input', () => {
    const empty = new Set<number>();
    const one = togglePick(empty, book(1));
    assert.deepEqual([...one], [1]);
    assert.equal(empty.size, 0);
    assert.deepEqual([...togglePick(one, book(1))], []);
  });

  test('ignores books already on the shelf', () => {
    assert.deepEqual([...togglePick(new Set([2]), book(1, true))], [2]);
  });

  test('keeps selections made under a previous search', () => {
    const afterFirst = togglePick(new Set(), book(1));
    const afterSecond = togglePick(afterFirst, book(7));
    assert.deepEqual([...afterSecond].sort(), [1, 7]);
  });
});

describe('mergePickerPages', () => {
  test('concatenates pages and drops duplicate ids', () => {
    const merged = mergePickerPages([[book(1), book(2)], [book(2), book(3)]]);
    assert.deepEqual(merged.map((b) => b.id), [1, 2, 3]);
  });
});

describe('retainFailed', () => {
  test('keeps only failed ids selected for retry', () => {
    assert.deepEqual([...retainFailed(new Set([1, 2, 3]), [2])], [2]);
    assert.deepEqual([...retainFailed(new Set([1, 2]), [])], []);
  });
});
