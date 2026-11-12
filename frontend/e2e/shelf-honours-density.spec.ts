import { test, expect, Page } from '@playwright/test';

/*
 * #980 — the Library's "Book density" view setting is stored in localStorage
 * (cwng:catalog-density-v1). Shelves used to ignore it and always render their
 * own fixed grid. Density changes how many tracks fit the row, so the number of
 * resolved grid columns is a layout-independent way to see which density a
 * shelf is using, and it works with the single seeded book CI has (auto-fill
 * resolves empty tracks too).
 */

async function csrfToken(page: Page): Promise<string> {
  const res = await page.request.get('/api/v1/auth/csrf');
  return ((await res.json()) as { csrf_token: string }).csrf_token;
}

test('#980 a shelf follows the density chosen on the Library page', async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 800 });
  const headers = { 'X-CSRFToken': await csrfToken(page) };

  const booksRes = await page.request.get('/api/v1/books?per_page=1');
  const books = (await booksRes.json()) as { total: number; items: Array<{ id: number }> };
  test.skip((books.total ?? 0) < 1, 'no seeded book to shelve');

  const created = await page.request.post('/api/v1/shelves', {
    headers,
    data: { name: `e2e-980-${Date.now()}` },
  });
  expect(created.ok(), 'shelf create should succeed').toBeTruthy();
  const shelfId = ((await created.json()) as { id: number }).id;

  try {
    const add = await page.request.post(
      `/api/v1/shelves/${shelfId}/books/${books.items[0].id}`, { headers });
    expect(add.ok(), 'adding the book to the shelf should succeed').toBeTruthy();

    // Same origin as the app, so this is the Library's own storage key.
    await page.goto('/app');
    const columnsFor = async (density: string | null) => {
      await page.evaluate((value) => {
        if (value === null) localStorage.removeItem('cwng:catalog-density-v1');
        else localStorage.setItem('cwng:catalog-density-v1', value);
      }, density);
      await page.goto(`/app/shelf/${shelfId}`);
      const grid = page.getByTestId('shelf-grid');
      await expect(grid).toBeVisible();
      return grid.evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(' ').length);
    };

    const comfortable = await columnsFor('comfortable');
    const compact = await columnsFor('compact');
    const dense = await columnsFor('dense');
    expect(dense).toBeGreaterThan(comfortable);
    expect(compact).toBeGreaterThanOrEqual(comfortable);
    expect(dense).toBeGreaterThanOrEqual(compact);

    // No stored choice behaves like the Library's default, Compact.
    expect(await columnsFor(null)).toBe(compact);
  } finally {
    await page.request.post(`/api/v1/shelves/${shelfId}/delete`, { headers }).catch(() => {});
  }
});
