import { describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { QueryClient, QueryObserver } from '@tanstack/react-query';

import { createEntityListQueryOptions } from '../../src/lib/entityListQueryOptions.ts';

const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function observedCallCount(plural: string): Promise<number> {
  let calls = 0;
  const queryFn = async () => {
    calls += 1;
    return { items: [] };
  };
  const options = createEntityListQueryOptions(plural, queryFn);
  const client = new QueryClient();
  const observer = new QueryObserver(client, options);
  let resolveSuccess!: () => void;
  const success = new Promise<void>((resolve) => { resolveSuccess = resolve; });
  const unsubscribe = observer.subscribe((result) => {
    if (result.isSuccess) resolveSuccess();
  });

  try {
    if (plural === '') {
      await delay(50);
    } else {
      let timeout: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([
          success,
          new Promise<never>((_, reject) => {
            timeout = setTimeout(() => reject(new Error('enabled entity query did not settle')), 1_000);
          }),
        ]);
      } finally {
        if (timeout) clearTimeout(timeout);
      }
    }
    return calls;
  } finally {
    unsubscribe();
    client.clear();
  }
}

describe('createEntityListQueryOptions', () => {
  test('runs only when an entity endpoint is present', () => {
    const queryFn = async () => ({ items: [] });

    assert.equal(createEntityListQueryOptions('', queryFn).enabled, false);
    assert.equal(createEntityListQueryOptions('authors', queryFn).enabled, true);
  });

  test('preserves the entity-list cache contract', () => {
    const queryFn = async () => ({ items: [] });

    for (const plural of ['', 'authors']) {
      const options = createEntityListQueryOptions(plural, queryFn);
      assert.deepEqual(options.queryKey, ['entities', plural]);
      assert.equal(options.staleTime, 60000);
      assert.equal(options.queryFn, queryFn);
    }
  });

  test('a real observer fetches only for a non-empty entity endpoint', async () => {
    assert.equal(await observedCallCount(''), 0);
    assert.equal(await observedCallCount('authors'), 1);
  });
});
