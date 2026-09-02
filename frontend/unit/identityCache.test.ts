import assert from 'node:assert/strict';
import test from 'node:test';
import { QueryClient } from '@tanstack/react-query';

import type { Me } from '../src/lib/api.ts';
import {
  replaceCachedIdentity,
} from '../src/lib/identityCache.ts';
import {
  loadCatalog,
  saveCatalog,
} from '../src/lib/scrollCache.ts';

const administrator = { id: 1, name: 'administrator' } as Me;
const secondary = { id: 2, name: 'secondary' } as Me;

async function preFixIdentityTransition(client: QueryClient, next: Me) {
  // Reproduce queries.ts before this fix: publish the new identity first,
  // then begin an unawaited, root-by-root purge. Even before the first await
  // resumes, observers can pair the secondary identity with administrator data.
  client.setQueryData(['me'], next);
  void (async () => {
    for (const key of [
      'about', 'books', 'book', 'global-library', 'account', 'shelves', 'shelf',
    ]) {
      await client.cancelQueries({ queryKey: [key] });
      client.removeQueries({ queryKey: [key] });
    }
  })();
}

test('the pre-fix identity-first ordering exposes old library data', async () => {
  const client = new QueryClient();
  client.setQueryData(['me'], administrator);
  client.setQueryData(['books', 1, 24], {
    items: [{ id: 101, title: 'administrator-only cached book' }], total: 1,
  });
  let leaked = false;
  const unsubscribe = client.getQueryCache().subscribe(() => {
    const me = client.getQueryData<Me | null>(['me']);
    const books = client.getQueriesData({ queryKey: ['books'] });
    if (me?.id === secondary.id && books.some(([, data]) => data !== undefined)) {
      leaked = true;
    }
  });

  await preFixIdentityTransition(client, secondary);
  unsubscribe();

  assert.equal(leaked, true);
});

test('the fixed identity switch never publishes the new account beside old library data', async () => {
  const client = new QueryClient();
  client.setQueryData(['me'], administrator);
  client.setQueryData(['books', 1, 24], {
    items: [{ id: 101, title: 'administrator-only cached book' }], total: 1,
  });
  client.setQueryData(['global-library', 1, 24], {
    items: [{ id: 101, title: 'administrator-only cached book' }], total: 1,
  });
  client.setQueryData(['book', '101'], {
    id: 101,
    title: 'administrator-only cached detail',
    using_my_cover: true,
    cover_url: '/api/v1/books/101/my-cover/image?c=1',
  });
  client.setQueryData(['cover-state', '101'], {
    using_my_cover: true,
    cover_url: '/api/v1/books/101/my-cover/image?c=1',
  });
  client.setQueryData(['notices', 'all'], { notices: [{ id: 9 }] });
  saveCatalog('catalog:::', {
    resetKey: 'old', page: 1,
    books: [{ id: 101, title: 'administrator-only cached book' } as never],
    scrollY: 0, search: '', searchInput: '', sort: 'new', readFilter: 'all',
    membershipFiltered: false,
  });

  let leaked = false;
  const unsubscribe = client.getQueryCache().subscribe(() => {
    const me = client.getQueryData<Me | null>(['me']);
    const books = client.getQueriesData({ queryKey: ['books'] });
    if (me?.id === secondary.id && books.some(([, data]) => data !== undefined)) {
      leaked = true;
    }
  });

  await replaceCachedIdentity(client, secondary);
  unsubscribe();

  assert.equal(leaked, false);
  assert.equal(client.getQueryData<Me>(['me'])?.id, secondary.id);
  assert.deepEqual(client.getQueriesData({ queryKey: ['books'] }), []);
  assert.deepEqual(client.getQueriesData({ queryKey: ['global-library'] }), []);
  assert.deepEqual(client.getQueriesData({ queryKey: ['book'] }), []);
  assert.deepEqual(client.getQueriesData({ queryKey: ['cover-state'] }), []);
  assert.deepEqual(client.getQueriesData({ queryKey: ['notices'] }), []);
  assert.equal(loadCatalog('catalog:::'), undefined);
});
