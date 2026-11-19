import { randomUUID } from 'node:crypto';
import { test, expect } from '@playwright/test';

test('a shelf can display books in series order and return to manual order (#2840)', async ({ page }) => {
  const listing = await page.request.get('/api/v1/books?per_page=100');
  expect(listing.ok(), await listing.text()).toBeTruthy();
  const { items } = await listing.json() as {
    items: { id: number; series: string | null; series_index: number }[];
  };
  const series = items.filter(book => book.series === 'E2E Fixture Series');
  const first = series.find(book => book.series_index === 1);
  const second = series.find(book => book.series_index === 2);
  const third = items.find(book => book.id !== first?.id && book.id !== second?.id && Number.isFinite(book.series_index));
  if (!first || !second || !third) throw new Error('Expected the two series fixtures and a third book');
  const ascending = [first, second, third]
    .sort((a, b) => a.series_index - b.series_index || a.id - b.id)
    .map(book => book.id);
  const reverse = [...ascending].reverse();
  // Neither series sort should match the manual order.
  const manual = [ascending[1], ascending[2], ascending[0]];

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
    await sort.selectOption({ label: 'Series order' });
    await expect.poll(renderedIds).toEqual(ascending);
    await page.reload();
    await expect(sort).toHaveValue('seriesasc');
    await expect.poll(renderedIds).toEqual(ascending);

    await sort.selectOption({ label: 'Series order (reverse)' });
    await expect.poll(renderedIds).toEqual(reverse);
    await sort.selectOption({ label: 'Manual order' });
    await expect.poll(renderedIds).toEqual(manual);
  } finally {
    const deleted = await page.request.post(`/api/v1/shelves/${id}/delete`, { headers });
    expect(deleted.ok(), await deleted.text()).toBeTruthy();
  }
});
