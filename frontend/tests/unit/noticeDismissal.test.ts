import { describe, test } from 'node:test';
import assert from 'node:assert/strict';

import {
  NOTICE_DISMISS_BATCH_SIZE,
  dismissNoticeIdsInBatches,
} from '../../src/lib/noticeDismissal.ts';

describe('dismissNoticeIdsInBatches', () => {
  test('keeps the normal happy path to one request', async () => {
    const calls: number[][] = [];
    const result = await dismissNoticeIdsInBatches([101, 102], async (ids) => {
      calls.push(ids);
      return { dismissed: ids.length, remaining: 0 };
    });

    assert.deepEqual(calls, [[101, 102]]);
    assert.deepEqual(result, { dismissed: 2, remaining: 0 });
  });

  test('dismisses the reported 872-notice shape in API-safe batches', async () => {
    const ids = Array.from({ length: 872 }, (_, index) => index + 1);
    const calls: number[][] = [];
    const result = await dismissNoticeIdsInBatches(ids, async (batch) => {
      calls.push(batch);
      return { dismissed: batch.length, remaining: ids.length - calls.flat().length };
    });

    assert.deepEqual(calls.map((batch) => batch.length), [500, 372]);
    assert.ok(calls.every((batch) => batch.length <= NOTICE_DISMISS_BATCH_SIZE));
    assert.deepEqual(calls.flat(), ids);
    assert.deepEqual(result, { dismissed: 872, remaining: 0 });
  });

  test('stops after the first failed batch so later IDs are never reported as dismissed', async () => {
    const ids = Array.from({ length: 872 }, (_, index) => index + 1);
    let calls = 0;

    await assert.rejects(
      dismissNoticeIdsInBatches(ids, async () => {
        calls += 1;
        throw new Error('dismissal failed');
      }),
      /dismissal failed/,
    );
    assert.equal(calls, 1);
  });
});
