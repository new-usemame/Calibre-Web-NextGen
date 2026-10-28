import assert from 'node:assert/strict';
import test from 'node:test';

import { mergeRequestIds } from '../src/lib/duplicateMerge.ts';

// /ajax/mergebooks keeps the FIRST id and deletes every other one, so the order
// of this list decides which book survives. Getting it wrong deletes the copy
// the reader chose to keep.
test('the chosen copy is sent first, whatever its position in the group', () => {
  assert.deepEqual(mergeRequestIds([11, 12, 13], 13, [11, 12, 13]), [13, 11, 12]);
  assert.deepEqual(mergeRequestIds([11, 12, 13], 12, [11, 12, 13]), [12, 11, 13]);
});

test('copies left unticked are not merged or deleted', () => {
  assert.deepEqual(mergeRequestIds([11, 12, 13], 11, [11, 13]), [11, 13]);
});

test('ticking the kept copy is ignored rather than sending it twice', () => {
  assert.deepEqual(mergeRequestIds([11, 12], 11, [11, 12, 11]), [11, 12]);
});

test('nothing to merge yields no request', () => {
  assert.equal(mergeRequestIds([11, 12], 11, [11]), null);
  assert.equal(mergeRequestIds([11, 12], 11, []), null);
});

test('a kept id or ticked id outside the group is refused', () => {
  // A stale selection from a group that has since changed must not reach the
  // server, where it would delete a book the reader can no longer see here.
  assert.equal(mergeRequestIds([11, 12], 99, [11, 12]), null);
  assert.deepEqual(mergeRequestIds([11, 12], 11, [12, 99]), [11, 12]);
});
