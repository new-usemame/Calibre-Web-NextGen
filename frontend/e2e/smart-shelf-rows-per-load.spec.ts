import { test, expect, Page } from '@playwright/test';

/*
 * #980 — Smart shelves follow the Library's density, and "Rows per load"
 * (cwng:catalog-rows-v1) should size their pages too. A smart shelf asked the
 * server for its default page size whatever was chosen. It should ask for whole
 * rows: per_page = rows x the columns the grid really renders, in a single first
 * request (no guess-then-refetch). The request size is checked rather than
 * scrolling a long shelf, so it works with the single seeded book CI has;
 * auto-fill resolves empty tracks, so the column count is still measurable.
 */

async function csrfToken(page: Page): Promise<string> {
  const res = await page.request.get('/api/v1/auth/csrf');
  return ((await res.json()) as { csrf_token: string }).csrf_token;
}

test('#980 a smart shelf requests rows x columns books, in one first request', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 800 });
  const headers = { 'X-CSRFToken': await csrfToken(page) };

  const booksRes = await page.request.get('/api/v1/books?per_page=1');
  const books = (await booksRes.json()) as { total: number };
  test.skip((books.total ?? 0) < 1, 'no seeded book for the smart shelf to match');

  // A rule every book satisfies, in the shape the classic builder posts.
  const created = await page.request.post('/magicshelf', {
    headers,
    data: {
      name: `e2e-980-smart-${Date.now()}`,
      icon: '🪄',
      rules: {
        condition: 'AND',
        rules: [{ id: 'tag', field: 'tag', type: 'string', input: 'text', operator: 'not_contains', value: 'e2e-980-never' }],
        valid: true,
      },
    },
  });
  expect(created.ok(), 'smart shelf create should succeed').toBeTruthy();
  const shelfId = ((await created.json()) as { shelf_id?: number }).shelf_id;
  expect(shelfId, 'smart shelf create should return an id').toBeTruthy();

  try {
    await page.goto('/app');
    const openShelf = async (density: string, rows: string) => {
      await page.evaluate(([d, r]) => {
        localStorage.setItem('cwng:catalog-density-v1', d);
        localStorage.setItem('cwng:catalog-rows-v1', r);
      }, [density, rows]);
      const firstPages: number[] = [];
      const onRequest = (request: import('@playwright/test').Request) => {
        const url = new URL(request.url());
        if (url.pathname !== `/api/v1/magicshelf/${shelfId}`) return;
        if (url.searchParams.get('select_all') || url.searchParams.get('page') !== '1') return;
        firstPages.push(Number(url.searchParams.get('per_page')));
      };
      page.on('request', onRequest);
      await page.goto(`/app/magic/${shelfId}`);
      const grid = page.getByTestId('magic-shelf-grid');
      await expect(grid).toBeVisible();
      page.off('request', onRequest);
      const columns = await grid.evaluate(
        (el) => getComputedStyle(el).gridTemplateColumns.split(' ').length);
      return { firstPages, columns };
    };

    for (const density of ['comfortable', 'dense']) {
      const one = await openShelf(density, '1');
      expect(one.firstPages, `${density}: one first-page request, no refetch`).toHaveLength(1);
      expect(one.firstPages[0]).toBe(1 * one.columns);

      const three = await openShelf(density, '3');
      expect(three.firstPages).toHaveLength(1);
      expect(three.firstPages[0]).toBe(3 * three.columns);
    }
  } finally {
    await page.request.post(`/magicshelf/${shelfId}/delete`, { headers }).catch(() => {});
  }
});
