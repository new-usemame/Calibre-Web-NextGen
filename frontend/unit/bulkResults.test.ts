import assert from 'node:assert/strict';
import test from 'node:test';

import {
  joinBulkSentences,
  presentBulkFailures,
  settleById,
} from '../src/lib/bulkResults.ts';

test('settleById retains every item value, warning, and failure for truthful callers', async () => {
  const injected = new Error('network unavailable');
  const result = await settleById(
    [31, 32, 33],
    async (id) => {
      if (id === 32) throw injected;
      if (id === 33) {
        return { deleted: true, warning: { code: 'cleanup_incomplete', message: 'files remain' } };
      }
      return { deleted: true };
    },
    {
      warningFor: (id, value) => value.warning
        ? { id, ...value.warning }
        : undefined,
    },
  );

  assert.deepEqual(result.succeededIds, [31]);
  assert.deepEqual(result.warningIds, [33]);
  assert.deepEqual(result.failedIds, [32]);
  assert.deepEqual(result.outcomes.map(({ id, status }) => ({ id, status })), [
    { id: 31, status: 'succeeded' },
    { id: 32, status: 'failed' },
    { id: 33, status: 'warning' },
  ]);
  assert.equal(result.outcomes[0].status === 'succeeded' && result.outcomes[0].value.deleted, true);
  assert.equal(result.outcomes[1].status === 'failed' && result.outcomes[1].error, injected);
  assert.deepEqual(result.warnings, [
    { id: 33, code: 'cleanup_incomplete', message: 'files remain' },
  ]);
  assert.deepEqual(result.failureDetails, [
    { id: 32, message: 'network unavailable' },
  ]);
});

test('shared failure reasons are stated once and omitted from per-book items', () => {
  const canonical = 'The last book cannot be removed unless you can browse the global library.';
  const presentation = presentBulkFailures([
    {
      id: 6,
      code: 'library_membership_rejected',
      message: 'The last book cannot be removed unless this user can browse the global library.',
    },
    {
      id: 9,
      code: 'library_membership_rejected',
      message: 'A deliberately different server wording.',
    },
  ], () => canonical);

  assert.deepEqual(presentation, {
    sharedReason: 'The last book cannot be removed unless you can browse the global library',
    items: [{ id: 6 }, { id: 9 }],
  });
  const failures = presentation.items.map((item) => `Book ${item.id}`).join('; ');
  assert.equal(
    joinBulkSentences(
      '0 book(s) removed from your library; 2 failed.',
      presentation.sharedReason ?? '',
      `Failed: ${failures}. The failed books remain selected; choose the action again to retry.`,
    ),
    '0 book(s) removed from your library; 2 failed. '
      + 'The last book cannot be removed unless you can browse the global library. '
      + 'Failed: Book 6; Book 9. The failed books remain selected; choose the action again to retry.',
  );
});

test('failure presentation and sentence joining cannot create double punctuation', () => {
  const presentation = presentBulkFailures([
    { id: 6, message: 'Network unavailable..' },
    { id: 9, message: 'Permission denied!.' },
  ]);
  assert.deepEqual(presentation, {
    items: [
      { id: 6, reason: 'Network unavailable' },
      { id: 9, reason: 'Permission denied' },
    ],
  });

  const failures = presentation.items
    .map((item) => `Book ${item.id}: ${item.reason}`)
    .join('; ');
  assert.equal(
    joinBulkSentences('0 updated; 2 failed..', `Failed: ${failures}.`),
    '0 updated; 2 failed. Failed: Book 6: Network unavailable; Book 9: Permission denied.',
  );
  assert.doesNotMatch(joinBulkSentences('Done.', 'Still warning!.'), /[.!?]{2}/u);
});
