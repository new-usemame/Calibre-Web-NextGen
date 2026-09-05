import { test } from 'node:test';
import assert from 'node:assert/strict';
import { resumeCfi } from '../src/lib/readerResume.ts';

test('portable percentages reach epub.js as fractions, including zero and completion', () => {
  const fractions: number[] = [];
  const locations = { cfiFromPercentage: (fraction: number) => { fractions.push(fraction); return 'cfi'; } };
  for (const percentage of [0, 0.5, 37.5, 100]) {
    assert.equal(resumeCfi(locations, { percentage, mode: 'automatic', synced_at: '' }), 'cfi');
  }
  assert.deepEqual(fractions, [0, .005, .375, 1]);
  for (const percentage of [-1, 101, NaN, Infinity, true, '37']) {
    assert.equal(resumeCfi(locations, { percentage: percentage as number, mode: 'offer', synced_at: '' }), undefined);
  }
  assert.equal(fractions.length, 4);
});
