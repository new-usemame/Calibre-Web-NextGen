import { expect, test } from '@playwright/test';

// Presentation contract; actual source/selection/worker/ingest is covered by the
// separate current-image full-runtime probe and native authenticated browser flow.
for (const title of ['Original edition', 'Original' + 'X'.repeat(180)]) {
test(`publication summaries open details explicitly before a supported request (${title.length > 30 ? 'long title' : 'ordinary title'})`, async ({ page }) => {
  await page.route('**/api/v1/auth/me', async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), locale: 'en', acquisition_access: true } });
  });
  const reads: string[] = []; const writes: unknown[] = [];
  const book = { identity: 'original', title, authors: ['Original Writer'], languages: ['en'], description: null };
  const shape = (detail: boolean) => ({ title: 'Original books', protocol: 'opds2', navigation: [], pagination: [], searches: [], groups: [], facets: [], publications: [{ ...book,
    navigation: detail ? [] : [{ title, relations: ['self'], selection: 'owned-detail' }],
    offers: detail ? [{ format: 'EPUB', label: null, identity: 'file', relation: 'open-access', offer_id: 'owned-offer' }] : [],
  }] });
  await page.route('**/api/v1/acquisition**', async route => {
    const url = new URL(route.request().url()); let result: unknown;
    if (url.pathname.endsWith('/catalog')) { reads.push(url.searchParams.get('selection') || 'root'); result = shape(url.searchParams.get('selection') === 'owned-detail'); }
    else if (url.pathname.endsWith('/jobs') && route.request().method() === 'POST') { writes.push(route.request().postDataJSON()); result = { id: 'request', title: book.title, state: 'imported', result: { book_ids: [1], disposition: 'imported' } }; }
    else if (url.pathname.endsWith('/jobs')) result = { jobs: writes.length ? [{ id: 'request', title: book.title, state: 'imported', result: { book_ids: [1], disposition: 'imported' } }] : [] };
    else result = { connections: [{ id: 'source', label: 'Owned OPDS', adapter: 'opds', enabled: true, revision: 1 }], can_acquire: true, runtime: { available: true, reasons: [] } };
    await route.fulfill({ json: result });
  });
  await page.goto('/app/find-books');
  const card = page.locator('li').filter({ has: page.getByRole('heading', { name: book.title, exact: true }) });
  await expect(card.getByRole('button', { name: book.title, exact: true })).toBeVisible();
  await expect(card.getByText('No EPUB or PDF available from this catalog.')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(await page.evaluate(() => innerWidth));
  expect(reads).toEqual(['root']); expect(writes).toEqual([]);
  await card.getByRole('button', { name: book.title, exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(card.getByRole('button', { name: 'Download EPUB', exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Original books', exact: true })).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(await page.evaluate(() => innerWidth));
  expect(reads).toEqual(['root', 'owned-detail']); expect(writes).toEqual([]);
  await card.getByRole('button', { name: 'Download EPUB', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Open book', exact: true })).toBeVisible();
  expect(writes).toHaveLength(1); expect(writes[0]).toMatchObject({ connection_id: 'source', offer_id: 'owned-offer' });
});
}
