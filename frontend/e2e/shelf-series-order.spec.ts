import { randomUUID } from 'node:crypto';
import { test, expect } from './fixtures';

test('a shelf can display books in series order and return to manual order (#2840)', async ({ page, secondaryUser }) => {
  // Keep the project's desktop or touch viewport, while using an account no
  // other test can change. Hidden and archived books are per-account.
  await page.context().clearCookies();
  await page.context().addCookies(await secondaryUser.context.cookies());
  const listing = await page.request.get('/api/v1/books?per_page=100');
  expect(listing.ok(), await listing.text()).toBeTruthy();
  const { items } = await listing.json() as { items: { id: number }[] };
  expect(items.length).toBeGreaterThanOrEqual(3);
  const manual = [items[1].id, items[2].id, items[0].id];

  const csrf = await page.request.get('/api/v1/auth/csrf');
  expect(csrf.ok()).toBeTruthy();
  const headers = { 'X-CSRFToken': (await csrf.json()).csrf_token as string };
  const created = await page.request.post('/api/v1/shelves', {
    headers, data: { name: `Series order ${randomUUID()}` },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const { id } = await created.json();
  try {
    for (const bookId of manual) {
      const added = await page.request.post(`/api/v1/shelves/${id}/books/${bookId}`, { headers });
      expect(added.ok(), await added.text()).toBeTruthy();
    }

    await page.goto(`/app/shelf/${id}`);
    const sort = page.getByRole('combobox', { name: 'Sort order' });
    const cards = page.getByTestId('shelf-grid').locator('[data-book-id]');
    const renderedIds = () => cards.evaluateAll(nodes => nodes.map(node => Number(node.getAttribute('data-book-id'))));

    await expect(sort).toHaveValue('stored');
    await expect.poll(renderedIds).toEqual(manual);
    // Other Playwright workers edit the seed books. Use each real shelf
    // response's own metadata to verify its order without sharing a named series.
    const waitForSort = (value: string) => page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === `/api/v1/shelves/${id}` && url.searchParams.get('sort') === value;
    });
    const expectSeriesOrder = async (response: Awaited<ReturnType<typeof waitForSort>>, direction: 1 | -1) => {
      expect(response.ok()).toBeTruthy();
      const { items: sorted } = await response.json() as { items: { id: number; series_index: number | null }[] };
      expect(sorted.map(book => book.id).sort()).toEqual([...manual].sort());
      const expected = [...sorted].sort((a, b) =>
        direction * ((a.series_index ?? -Infinity) - (b.series_index ?? -Infinity) || a.id - b.id));
      expect(sorted.map(book => book.id)).toEqual(expected.map(book => book.id));
      await expect.poll(renderedIds).toEqual(expected.map(book => book.id));
    };

    await expect(sort.locator('option[value=seriesasc]')).toHaveText('Series order');
    const ascending = waitForSort('seriesasc');
    await sort.selectOption('seriesasc');
    await expectSeriesOrder(await ascending, 1);
    const reloaded = waitForSort('seriesasc');
    await page.reload();
    await expect(sort).toHaveValue('seriesasc');
    await expectSeriesOrder(await reloaded, 1);

    const reverse = waitForSort('seriesdesc');
    await sort.selectOption({ label: 'Series order (reverse)' });
    await expectSeriesOrder(await reverse, -1);
    await sort.selectOption({ label: 'Manual order' });
    await expect.poll(renderedIds).toEqual(manual);
  } finally {
    const deleted = await page.request.post(`/api/v1/shelves/${id}/delete`, { headers });
    expect(deleted.ok(), await deleted.text()).toBeTruthy();
  }
});
