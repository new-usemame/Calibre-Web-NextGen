import { test as base, expect } from './fixtures';
import type { Page } from '@playwright/test';
const test = base.extend<{ ownedPage: Page }>({
  ownedPage: async ({ browser, secondaryUser, baseURL }, use, info) => {
    const p = info.project.use;
    const context = await browser.newContext({ baseURL, viewport: p.viewport, isMobile: p.isMobile,
      hasTouch: p.hasTouch, userAgent: p.userAgent, storageState: await secondaryUser.context.storageState() });
    try { await use(await context.newPage()); } finally { await context.close(); }
  },
});
const endpoint = '/api/v1/account/discover-source';
for (const lateFailure of [false, true]) {
test(lateFailure ? 'a late old-source shelf failure cannot restore hidden selection after a Discover source Save' : 'a held old-source shelf picker cancels when a real Discover source Save completes', async ({ ownedPage: page, secondaryUser }) => {
  const csrf = await page.request.get('/api/v1/auth/csrf');
  expect(csrf.ok()).toBeTruthy();
  const headers = { 'X-CSRFToken': (await csrf.json()).csrf_token };
  const candidates = await page.request.get('/api/v1/books?filter=discover&per_page=100');
  expect(candidates.ok()).toBeTruthy();
  const books = (await candidates.json()).items as { id: number; title: string }[];
  expect(books.length).toBeGreaterThan(1);
  const [oldBook, newBook] = books;
  const shelfIds: number[] = [];
  async function makeShelf(label: string, bookId?: number) {
    const created = await page.request.post('/api/v1/shelves', { headers,
      data: { name: `Source ${label} ${secondaryUser.username}` } });
    expect(created.status()).toBe(201);
    const shelf = await created.json() as { id: number; name: string };
    shelfIds.push(shelf.id);
    if (bookId !== undefined) expect((await page.request.post(`/api/v1/shelves/${shelf.id}/books/${bookId}`, { headers })).ok()).toBeTruthy();
    return shelf;
  }
  let releaseWrite = () => {};
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let started!: () => void;
  const putStarted = new Promise<void>(resolve => { started = resolve; });
  try {
    const first = await makeShelf('old', oldBook.id);
    const second = await makeShelf('new', newBook.id);
    const target = await makeShelf('target');
    expect((await page.request.put(endpoint, { headers, data: { source: `shelf:${first.id}` } })).ok()).toBeTruthy();
    await page.goto('/app/discover');
    const oldCard = page.getByTestId('catalog-grid').locator(`[data-book-id="${oldBook.id}"]`);
    await expect(oldCard).toBeVisible();
    await page.getByRole('button', { name: 'Select', exact: true }).click();
    const selection = page.getByRole('button', { name: `Select ${oldBook.title}`, exact: true }).first();
    await selection.focus(); await page.keyboard.press('Space');
    await expect(page.getByRole('region', { name: '1 selected', exact: true })).toBeVisible();
    await page.route(`**${endpoint}`, async route => {
      if (route.request().method() === 'PUT') { started(); await held; }
      await route.continue();
    });
    await page.getByLabel('Discover source', { exact: true }).selectOption(`shelf:${second.id}`);
    await page.getByRole('button', { name: 'Save Discover source', exact: true }).click();
    await putStarted;
    // The old card is still real and actionable while the actual save response is held.
    const handle = oldCard.getByRole('button', { name: `Add ${oldBook.title} to a shelf`, exact: true });
    await handle.focus(); await page.keyboard.press('Enter');
    const picker = page.getByRole('dialog', { name: 'Add to shelf', exact: true });
    await expect(picker).toBeVisible();
    await expect(picker).toContainText('1 selected');
    let failedResponse;
    if (lateFailure) {
      const writeEndpoint = `/api/v1/shelves/${target.id}/books/${oldBook.id}`;
      let writeStarted!: () => void;
      const startedWrite = new Promise<void>(resolve => { writeStarted = resolve; });
      const heldWrite = new Promise<void>(resolve => { releaseWrite = resolve; });
      await page.route(`**${writeEndpoint}`, async route => {
        writeStarted(); await heldWrite;
        await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'Owned late shelf failure' }) });
      });
      failedResponse = page.waitForResponse(response => response.url().endsWith(writeEndpoint));
      await picker.getByRole('button', { name: target.name, exact: true }).click();
      await startedWrite;
    }
    release();
    await expect(page.getByTestId('catalog-grid').locator(`[data-book-id="${newBook.id}"]`)).toBeVisible();
    await expect(oldCard).toHaveCount(0);
    if (lateFailure) {
      releaseWrite(); await failedResponse;
      await expect(page.getByTestId('catalog-grid').locator(`[data-book-id="${newBook.id}"]`).getByRole('button', {
        name: `Add ${newBook.title} to a shelf`, exact: true,
      })).toBeEnabled();
    }
    await expect(page.getByRole('region', { name: '1 selected', exact: true })).not.toBeVisible();
    await expect(picker).toHaveCount(0);
    const membership = await page.request.get(`/api/v1/shelves/${target.id}?per_page=100`);
    expect(membership.ok()).toBeTruthy(); expect((await membership.json()).items).toEqual([]);
  } finally {
    release(); releaseWrite(); await page.unrouteAll({ behavior: 'wait' });
    for (const id of shelfIds) expect((await page.request.post(`/api/v1/shelves/${id}/delete`, { headers })).ok()).toBeTruthy();
  }
});

}
