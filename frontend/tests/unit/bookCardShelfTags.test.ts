/*
 * Component regression tests for fork #1254: the shelf tags the classic grid
 * drew on each cover, which the new UI's cards never showed. Renders the real
 * BookCard through Vite's SSR loader, as bookCardReadingBadge.test.ts does.
 */
import { after, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { Router } from 'wouter';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import type { Book } from '../../src/lib/api.ts';

const vite = await createServer({
  root: fileURLToPath(new URL('../..', import.meta.url)),
  logLevel: 'silent',
  appType: 'custom',
  server: { middlewareMode: true },
});

after(async () => {
  await vite.close();
});

const { BookCard } = await vite.ssrLoadModule('/src/components/BookCard.tsx') as {
  BookCard: (props: Record<string, unknown>) => ReturnType<typeof createElement>;
};

function renderCard(shelves: Book['shelves'], props: Record<string, unknown> = {}): string {
  const book = {
    id: 1254,
    title: 'The Test Book',
    authors: ['Test Author'],
    series: null,
    series_index: null,
    cover_url: null,
    formats: [],
    shelves,
  } as Book;
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queryClient.setQueryData(['me'], null);
  return renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client: queryClient },
      createElement(
        Router,
        { hook: () => ['/', () => undefined] },
        createElement(BookCard, { book, ...props }),
      ),
    ),
  );
}

// The hover title carries the shelf names verbatim (the aria-label is the same
// names inside a translatable sentence, which this provider-less render leaves
// uninterpolated).
const tagLabels = (markup: string) =>
  [...markup.matchAll(/<span class="[^"]*shelfBadge[^"]*" role="img" aria-label="[^"]*" title="([^"]*)"/g)]
    .map((m) => m[1]);

describe('BookCard shelf tags (#1254)', () => {
  test('names the shelf a book sits on', () => {
    const markup = renderCard([{ id: 3, name: 'Etagère Lolo' }]);
    assert.match(markup, /data-testid="shelf-tags"/);
    assert.deepEqual(tagLabels(markup), ['Etagère Lolo']);
    assert.match(markup, />Etagère Lolo</);
  });

  test('folds shelves past the second into one +N tag that names them', () => {
    const markup = renderCard([
      { id: 1, name: 'A' }, { id: 2, name: 'B' }, { id: 3, name: 'C' }, { id: 4, name: 'D' },
    ]);
    assert.deepEqual(tagLabels(markup), ['A', 'B', 'C, D']);
    assert.match(markup, />\+2</);
  });

  test('draws nothing for an unshelved book or an older server', () => {
    assert.doesNotMatch(renderCard([]), /data-testid="shelf-tags"/);
    assert.doesNotMatch(renderCard(undefined), /data-testid="shelf-tags"/);
  });

  test('the Show shelf tags setting turns them off', () => {
    const markup = renderCard([{ id: 3, name: 'Etagère Lolo' }], { hideShelfTags: true });
    assert.doesNotMatch(markup, /data-testid="shelf-tags"/);
  });

  test("a shelf's own page leaves out that shelf, keeping the others", () => {
    const shelves = [{ id: 3, name: 'Etagère Lolo' }, { id: 9, name: 'Holiday' }];
    assert.deepEqual(tagLabels(renderCard(shelves, { excludeShelfId: 3 })), ['Holiday']);
    assert.doesNotMatch(
      renderCard([{ id: 3, name: 'Etagère Lolo' }], { excludeShelfId: 3 }),
      /data-testid="shelf-tags"/,
    );
  });
});
