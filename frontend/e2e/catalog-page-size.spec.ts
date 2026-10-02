import { test, expect } from './fixtures';

test('catalog fetches the selected row count times its measured columns after resize', async ({ secondaryUser }) => {
  const { page } = secondaryUser;
  let lastPageSize = 0;
  let requests = 0;
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/api/v1/books' && !url.searchParams.has('filter')) {
      lastPageSize = Number(url.searchParams.get('per_page'));
      requests++;
    }
  });
  for (const width of [1280, 375]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/app');
    const grid = page.getByTestId('catalog-grid');
    await expect(grid.locator('a[href*="/book/"]').first()).toBeVisible();
    await page.getByTestId('catalog-view-settings').click();
    const rows = page.getByRole('group', { name: 'Rows per load' });
    await rows.getByRole('radio', { name: '4', exact: true }).check();
    const columns = Number(await grid.getAttribute('data-catalog-column-count'));
    expect(columns).toBeGreaterThan(0);
    const before = requests;
    await rows.getByRole('radio', { name: '2', exact: true }).check();
    await expect.poll(() => requests).toBeGreaterThan(before);
    await expect.poll(() => lastPageSize).toBe(2 * columns);
    expect(await page.evaluate(() => localStorage.getItem('cwng:catalog-rows-v1'))).toBe('2');
  }
});
