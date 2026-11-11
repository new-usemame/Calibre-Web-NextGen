import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';
import { test } from 'node:test';
import { InfiniteQueryObserver, QueryClient } from '@tanstack/react-query';
import { createPagedEntityListQueryOptions } from '../../src/lib/entityListQueryOptions.ts';
import type { EntityListPage } from '../../src/lib/entityListQueryOptions.ts';
import { observeIntersection } from '../../src/lib/useIntersectionObserver.ts';

const page = (next_offset: number | null): EntityListPage => ({
  items: [{ id: next_offset === null ? 201 : 1, name: 'Author', count: 4 }],
  total: 201, unfiltered_total: 500, next_offset,
});

test('an intersecting sentinel requests the next server offset and appends the last page', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const urls: string[] = [];
  const observer = new InfiniteQueryObserver(client, createPagedEntityListQueryOptions(
    'authors', '', null, 1, async (url) => {
      urls.push(url);
      return page(url.includes('offset=0') ? 200 : null);
    },
  ));
  const unsubscribe = observer.subscribe(() => {});
  let intersect: (visible: boolean) => void = () => {};
  let disconnected = false;
  const sentinel = {} as HTMLDivElement;
  const original = globalThis.IntersectionObserver;
  // Keep browser geometry out of this unit test; use the production callback.
  class FakeIntersectionObserver {
    constructor(callback: IntersectionObserverCallback, options: IntersectionObserverInit) {
      assert.deepEqual(options, { threshold: 0.1, rootMargin: '200px' });
      intersect = (visible) => callback([{ isIntersecting: visible } as IntersectionObserverEntry],
        this as unknown as IntersectionObserver);
    }
    observe(target: Element) { assert.equal(target, sentinel); }
    disconnect() { disconnected = true; }
  }
  globalThis.IntersectionObserver = FakeIntersectionObserver as unknown as typeof IntersectionObserver;
  let cleanup = () => {};
  try {
    await observer.refetch();
    cleanup = observeIntersection(sentinel, () => { void observer.fetchNextPage(); }, 0.1, '200px');
    intersect(false);
    assert.equal(urls.length, 1);
    intersect(true);
    await delay(0);
    assert.deepEqual(urls, ['/api/v1/authors?limit=200&offset=0', '/api/v1/authors?limit=200&offset=200']);
    assert.deepEqual(observer.getCurrentResult().data?.pages.flatMap(p => p.items.map(i => i.id)), [1, 201]);
    assert.equal(observer.getCurrentResult().hasNextPage, false);
    assert.equal(observer.getCurrentResult().data?.pages[0].total, 201);
    assert.equal(observer.getCurrentResult().data?.pages[0].unfiltered_total, 500);
  } finally {
    cleanup();
    globalThis.IntersectionObserver = original;
    unsubscribe();
    client.clear();
  }
  assert.equal(disconnected, true);
});

test('name and inclusive minimum filters reach the server on every page and reset the offset', async () => {
  for (const plural of ['authors', 'series']) {
    const client = new QueryClient();
    const requests: URLSearchParams[] = [];
    const fetchPage = async (url: string, signal: AbortSignal) => {
      assert.ok(signal instanceof AbortSignal);
      requests.push(new URLSearchParams(url.split('?')[1]));
      return page(requests.length === 1 ? 200 : null);
    };
    const options = (q: string) => createPagedEntityListQueryOptions(plural, q, 3, 42, fetchPage);
    const observer = new InfiniteQueryObserver(client, options('Le & %_'));
    const unsubscribe = observer.subscribe(() => {});
    try {
      await observer.refetch();
      await observer.fetchNextPage();
      assert.deepEqual(requests.map(params => Object.fromEntries(params)), [
        { limit: '200', offset: '0', q: 'Le & %_', min_count: '3' },
        { limit: '200', offset: '200', q: 'Le & %_', min_count: '3' },
      ]);
      observer.setOptions(options('Moon'));
      await observer.refetch();
      assert.equal(requests.at(-1)?.get('q'), 'Moon');
      assert.equal(requests.at(-1)?.get('offset'), '0');
      assert.equal(observer.getCurrentResult().data?.pages.length, 1);
    } finally {
      unsubscribe();
      client.clear();
    }
  }
});

test('invalid minimums disable requests, including a filter change while invalid', async () => {
  const client = new QueryClient();
  let calls = 0;
  const fetchPage = async () => { calls++; return page(null); };
  const observer = new InfiniteQueryObserver(client, createPagedEntityListQueryOptions('authors', '', null, 1, fetchPage));
  const unsubscribe = observer.subscribe(() => {});
  try {
    await observer.refetch();
    assert.equal(calls, 1);
    for (const minimum of [0, -1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
      observer.setOptions(createPagedEntityListQueryOptions('authors', 'changed filter', minimum, 1, fetchPage));
      await delay(0);
    }
    assert.equal(calls, 1);
  } finally {
    unsubscribe();
    client.clear();
  }
});

test('other lists never enable paging and full-list caches cannot collide', () => {
  for (const plural of ['tags', 'publishers', 'languages', 'ratings', 'formats', '']) {
    const options = createPagedEntityListQueryOptions(plural, '', null, 1, async () => page(null));
    assert.equal(options.enabled, false);
    assert.deepEqual(options.queryKey.slice(0, 3), ['entities', plural, 'pages']);
  }
});
