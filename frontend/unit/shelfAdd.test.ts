import assert from 'node:assert/strict';
import test from 'node:test';
import { addShelfBooks, draggedBookIds } from '../src/lib/shelfAdd.ts';

test('a shelf permission/membership conflict remains a retryable failure', async () => {
  const result = await addShelfBooks([1, 2, 3, 4], async (id) => {
    if (id === 1) throw Object.assign(new Error('Already present'), { status: 409, detail: { code: 'conflict' } });
    if (id === 2) throw Object.assign(new Error('Not in your library'), { status: 409, detail: { code: 'library_membership_required' } });
    if (id === 3) throw Object.assign(new Error('No permission'), { status: 403 });
  });
  assert.deepEqual(result.succeededIds, [1, 4]);
  assert.deepEqual(result.failedIds, [2, 3]);
  assert.equal(result.failureDetails[0].code, 'library_membership_required');
});

test('twenty books retain display order and use at most four requests concurrently', async () => {
  let active = 0;
  let maximum = 0;
  const ids = Array.from({ length: 20 }, (_, i) => i + 1);
  const result = await addShelfBooks(ids, async () => {
    maximum = Math.max(maximum, ++active);
    await new Promise(resolve => setTimeout(resolve, 2));
    active--;
  });
  assert.deepEqual(result.succeededIds, ids);
  assert.ok(maximum <= 4, `observed ${maximum} simultaneous requests`);
});

test('dragging a selected card carries the whole selection; another card carries itself', () => {
  assert.deepEqual(draggedBookIds(2, [3, 2, 1, 2]), [3, 2, 1]);
  assert.deepEqual(draggedBookIds(4, [3, 2, 1]), [4]);
});
