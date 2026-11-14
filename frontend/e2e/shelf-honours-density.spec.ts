import { test, expect, Page } from '@playwright/test';

/*
 * #980 — the Library's "Book density" view setting is stored in localStorage
 * (cwng:catalog-density-v1). Shelves and Smart shelves used to ignore it and
 * always render their own fixed grid. Density changes how many tracks fit the
 * row, so the number of resolved grid columns is a layout-independent way to
 * see which density a grid is using, and it works with the single seeded book
 * CI has (auto-fill resolves empty tracks too).
 */

async function csrfToken(page: Page): Promise<string> {
  const res = await page.request.get('/api/v1/auth/csrf');
  return ((await res.json()) as { csrf_token: string }).csrf_token;
}

async function expectGridFollowsDensity(page: Page, url: string, testId: string) {
  // Same origin as the app, so this is the Library's own storage key.
  await page.goto('/app');
  const columnsFor = async (density: string | null) => {
    await page.evaluate((value) => {
      if (value === null) localStorage.removeItem('cwng:catalog-density-v1');
      else localStorage.setItem('cwng:catalog-density-v1', value);
    }, density);
    await page.goto(url);
    const grid = page.getByTestId(testId);
    await expect(grid).toBeVisible();
    return grid.evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(' ').length);
  };

  await page.setViewportSize({ width: 1200, height: 800 });
  const comfortable = await columnsFor('comfortable');
  const compact = await columnsFor('compact');
  const dense = await columnsFor('dense');
  expect(dense).toBeGreaterThan(comfortable);
  expect(compact).toBeGreaterThanOrEqual(comfortable);
  expect(dense).toBeGreaterThanOrEqual(compact);
  // No stored choice behaves like the Library's default, Compact.
  expect(await columnsFor(null)).toBe(compact);

  // Phones get the Library's fixed 2/3/4 columns, and Dense keeps its tighter
  // phone gap (--sp-2) rather than the desktop one.
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await columnsFor('comfortable')).toBe(2);
  expect(await columnsFor('compact')).toBe(3);
  expect(await columnsFor('dense')).toBe(4);
  const [gap, sp2] = await page.getByTestId(testId).evaluate((el) => {
    const probe = document.createElement('div');
    probe.style.width = 'var(--sp-2)';
    el.appendChild(probe);
    const width = probe.getBoundingClientRect().width;
    probe.remove();
    return [parseFloat(getComputedStyle(el).columnGap), width];
  });
  expect(gap).toBeCloseTo(sp2, 1);
}

test('#980 shelves and Smart shelves follow the density chosen on the Library page', async ({ page }) => {
  const headers = { 'X-CSRFToken': await csrfToken(page) };

  const booksRes = await page.request.get('/api/v1/books?per_page=1');
  const books = (await booksRes.json()) as { total: number; items: Array<{ id: number; title: string }> };
  test.skip((books.total ?? 0) < 1, 'no seeded book to shelve');
  const book = books.items[0];

  let shelfId: number | undefined;
  let smartId: number | undefined;
  try {
    const created = await page.request.post('/api/v1/shelves', {
      headers,
      data: { name: `e2e-980-${Date.now()}` },
    });
    expect(created.ok(), 'shelf create should succeed').toBeTruthy();
    shelfId = ((await created.json()) as { id: number }).id;
    const add = await page.request.post(`/api/v1/shelves/${shelfId}/books/${book.id}`, { headers });
    expect(add.ok(), 'adding the book to the shelf should succeed').toBeTruthy();

    const smart = await page.request.post('/magicshelf', {
      headers,
      data: {
        name: `e2e-980-smart-${Date.now()}`, icon: '📚',
        rules: { condition: 'AND', rules: [{ id: 'title', operator: 'equal', value: book.title }] },
      },
    });
    expect(smart.ok(), await smart.text()).toBeTruthy();
    smartId = ((await smart.json()) as { shelf_id: number }).shelf_id;

    await expectGridFollowsDensity(page, `/app/shelf/${shelfId}`, 'shelf-grid');
    await expectGridFollowsDensity(page, `/app/magic/${smartId}`, 'magic-shelf-grid');
  } finally {
    if (shelfId !== undefined) {
      await page.request.post(`/api/v1/shelves/${shelfId}/delete`, { headers }).catch(() => {});
    }
    if (smartId !== undefined) {
      await page.request.post(`/magicshelf/${smartId}/delete`, { headers }).catch(() => {});
    }
  }
});
