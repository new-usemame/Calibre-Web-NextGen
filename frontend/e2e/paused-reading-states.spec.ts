import { test as base, expect } from './fixtures';
import type { Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// secondaryUser owns identity and cleanup; copy its cookies into the actual
// project's device context so WebKit phone checks also exercise native touch.
const test = base.extend<{ statusPage: Page }>({
  statusPage: async ({ browser, secondaryUser, baseURL }, use, info) => {
    const profile = info.project.use;
    const context = await browser.newContext({
      baseURL,
      viewport: profile.viewport,
      isMobile: profile.isMobile,
      hasTouch: profile.hasTouch,
      deviceScaleFactor: profile.deviceScaleFactor,
      userAgent: profile.userAgent,
      storageState: await secondaryUser.page.context().storageState(),
    });
    try {
      await use(await context.newPage());
    } finally {
      await context.close();
    }
  },
});

async function post(page: Page, path: string, data: unknown) {
  const csrf = await page.request.get('/api/v1/auth/csrf');
  return page.request.post(path, { headers: { 'X-CSRFToken': (await csrf.json()).csrf_token }, data });
}
async function firstBook(page: Page) {
  const response = await page.request.get('/api/v1/books?per_page=8');
  expect(response.status()).toBe(200);
  const books = (await response.json()).items as { id: number; title: string }[];
  expect(books.length).toBeGreaterThan(0);
  return books[0];
}

test('a personal pause survives automatic progress and both filtered search paths', async ({ statusPage: page }) => {
  const book = await firstBook(page);
  await page.goto(`/app/book/${book.id}`);
  const status = page.getByRole('combobox', { name: 'Reading status', exact: true });
  await status.selectOption('on_hold');
  await expect(page.getByRole('status').filter({ hasText: 'Reading status updated' })).toBeVisible();
  expect((await post(page, `/api/v1/books/${book.id}/bookmark`, {
    format: 'epub', bookmark: 'epubcfi(/6/2!/4/2/1:0)', percentage: 100,
  })).status()).toBe(204);
  await page.reload();
  await expect(status).toHaveValue('on_hold');
  const bookmark = await page.request.get(`/api/v1/books/${book.id}/bookmark?format=epub`);
  expect(await bookmark.json()).toMatchObject({ bookmark: 'epubcfi(/6/2!/4/2/1:0)' });
  const violations = (await new AxeBuilder({ page }).include('[id=reading-status]').analyze()).violations;
  expect(violations.filter(v => v.impact === 'serious' || v.impact === 'critical')).toEqual([]);
  expect((await status.boundingBox())!.height).toBeGreaterThanOrEqual(44);

  await page.goto('/app');
  await page.getByRole('group', { name: 'Read status filter', exact: true }).getByRole('button', { name: 'On hold', exact: true }).click();
  await expect(page.locator(`main a[href="/app/book/${book.id}"]`).first()).toBeVisible();
  const filtered = await page.request.get('/api/v1/books?filter=on_hold&per_page=100');
  expect((await filtered.json()).items.map((b: { id: number }) => b.id)).toEqual([book.id]);
  await page.goto('/app/search');
  await page.getByRole('group', { name: 'Read status', exact: true }).getByRole('button', { name: 'On hold', exact: true }).click();
  const searched = page.waitForResponse(r => r.url().includes('/api/v1/search/advanced') && r.request().method() === 'POST');
  await page.locator('[data-testid=advanced-search-form] button[type=submit]').click();
  expect((await (await searched).json()).items.map((b: { id: number }) => b.id)).toEqual([book.id]);
  await expect(page).toHaveURL(/read_status=on_hold/);
  await page.reload();
  await expect(page.getByRole('group', { name: 'Read status', exact: true }).getByRole('button', { name: 'On hold', exact: true })).toHaveAttribute('aria-pressed', 'true');
});

test('Classic pause and explicit resume are reflected in the New UI', async ({ statusPage: page }) => {
  const book = await firstBook(page);
  await page.goto(`/book/${book.id}`);
  await page.locator('#read-status-select').selectOption('did_not_finish');
  await page.locator('#read-status-form button[type=submit]').click();
  await expect(page.locator('#did-not-finish-badge')).toBeVisible();
  await page.goto(`/app/book/${book.id}`);
  const status = page.getByRole('combobox', { name: 'Reading status', exact: true });
  await expect(status).toHaveValue('did_not_finish');
  await status.selectOption('in_progress');
  await expect(page.getByRole('status').filter({ hasText: 'Reading status updated' })).toBeVisible();
  await page.goto(`/book/${book.id}`);
  await expect(page.locator('#currently-reading-badge')).toBeVisible();
  await expect(page.locator('#read-status-select')).toHaveValue('in_progress');
});

test('a failed status save keeps the saved choice, associates the error and permits retry', async ({ statusPage: page }) => {
  const book = await firstBook(page);
  await page.goto(`/app/book/${book.id}`);
  const status = page.getByRole('combobox', { name: 'Reading status', exact: true });
  await expect(status).toHaveValue('unread');
  await page.route(`**/api/v1/books/${book.id}/read-status`, route => route.fulfill({
    status: 503, contentType: 'application/json', body: JSON.stringify({ error: { code: 'unavailable', message: 'Owned failure injection' } }),
  }));
  await status.selectOption('on_hold');
  await expect(page.locator('#reading-status-error')).toBeVisible();
  await expect(status).toHaveValue('unread');
  await expect(status).toHaveAttribute('aria-invalid', 'true');
  await expect(status).toHaveAttribute('aria-describedby', 'reading-status-help reading-status-error');
  await page.unroute(`**/api/v1/books/${book.id}/read-status`);
  await status.selectOption('on_hold');
  await expect(page.getByRole('status').filter({ hasText: 'Reading status updated' })).toBeVisible();
  await expect(status).toHaveValue('on_hold');
  await expect(status).not.toHaveAttribute('aria-invalid', 'true');
});


test('cover actions report paused choices and keep explicit finished and unread actions', async ({ statusPage: page }) => {
  const book = await firstBook(page);
  for (const [choice, label] of [['on_hold', 'On hold'], ['did_not_finish', 'Did not finish']]) {
    expect((await post(page, `/api/v1/books/${book.id}/read-status`, { status: choice })).status()).toBe(200);
    await page.goto('/app');
    const card = page.getByTestId('catalog-grid').locator(`[data-book-id="${book.id}"]`);
    await card.getByRole('button', { name: `Actions for ${book.title}`, exact: true }).press('Enter');
    const dialog = page.getByRole('dialog', { name: `Actions for ${book.title}`, exact: true });
    await expect(dialog.getByText(label, { exact: true })).toBeVisible();
    await expect(dialog.getByText('Unread', { exact: true })).toHaveCount(0);
    // Keyboard focus can scroll the card while the dialog opens. Its fixed
    // panel must remain usable without moving it or forcing an offscreen action.
    await expect.poll(() => dialog.evaluate(node => {
      const box = node.getBoundingClientRect();
      return box.top >= 7.5 && box.bottom <= window.innerHeight - 7.5;
    })).toBe(true);
    await dialog.getByRole('button', { name: 'Mark as read', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect.poll(async () => (await (await page.request.get(`/api/v1/books/${book.id}`)).json()).read_status).toBe('finished');
    await card.getByRole('button', { name: `Actions for ${book.title}`, exact: true }).press('Enter');
    await expect(dialog.getByText('Read', { exact: true })).toBeVisible();
    await dialog.getByRole('button', { name: 'Mark as unread', exact: true }).click();
    await expect(dialog).toHaveCount(0);
    await expect.poll(async () => (await (await page.request.get(`/api/v1/books/${book.id}`)).json()).read_status).toBe('unread');
  }
});


test('a wrapped cover-action error stays inside the viewport after content growth and resizing', async ({ statusPage: page }, info) => {
  // The desktop panel floats above its opener; touch has a separate bottom-anchored layout.
  test.skip(!!info.project.use.hasTouch, 'Desktop floating-panel geometry');
  expect((await post(page, '/api/v1/account/profile', { locale: 'fr', theme: 'light' })).status()).toBe(200);
  const translation = await page.request.get('/api/v1/i18n/fr.json');
  expect(translation.ok()).toBeTruthy();
  const catalog = (await translation.json()).catalog as Record<string, string>;
  const t = (key: string) => catalog[key] ?? key;
  const errorText = t('Could not update this book. Try again.');
  expect(errorText).not.toBe('Could not update this book. Try again.');
  // The shared CI library has seven books. An owned shelf fixes the grid size
  // without creating, editing or deleting shared Calibre metadata.
  const response = await page.request.get('/api/v1/books?per_page=7');
  expect(response.ok()).toBeTruthy();
  const books = (await response.json()).items as { id: number; title: string }[];
  expect(books).toHaveLength(7);
  const created = await post(page, '/api/v1/shelves', { name: `Panel bounds ${info.testId}` });
  expect(created.status()).toBe(201);
  const shelf = await created.json() as { id: number };
  try {
    for (const book of books) {
      expect((await post(page, `/api/v1/shelves/${shelf.id}/books/${book.id}`, {})).ok()).toBeTruthy();
    }
    // A shorter desktop viewport puts the last real grid row below the fold,
    // so native scrolling can expose its entire trigger at the lower edge.
    await page.setViewportSize({ width: page.viewportSize()!.width, height: 600 });
    await page.goto(`/app/shelf/${shelf.id}`);
    const cards = page.locator('main [data-book-id]');
    await expect(cards.first()).toBeVisible();
    await expect(cards).toHaveCount(7);
    const card = cards.last();
    const id = await card.getAttribute('data-book-id');
    const detail = await page.request.get(`/api/v1/books/${id}`);
    expect(detail.ok()).toBeTruthy();
    const book = await detail.json() as { title: string };
    const opener = card.getByRole('button', {
      name: t('Actions for {title}').replace('{title}', book.title), exact: true,
    });
    // The shelf staggers entry animations: a stable frame during the delay
    // can still move after the wheel delta is measured. Use settled geometry.
    await expect.poll(() => card.evaluate(node => node.getAnimations().every(animation =>
      animation.playState === 'finished' || animation.playState === 'idle',
    ))).toBe(true);
    await opener.scrollIntoViewIfNeeded();
    const height = page.viewportSize()!.height;
    const initial = (await opener.boundingBox())!;
    // Send native scrolling over the actual card, with its complete trigger
    // at the viewport's lower edge before ordinary keyboard activation.
    await page.mouse.move(initial.x + initial.width / 2, initial.y + initial.height / 2);
    await page.mouse.wheel(0, initial.y - (height - initial.height - 1));
    await expect.poll(async () => {
      const box = (await opener.boundingBox())!;
      return box.y >= height - box.height - 3 && box.y + box.height <= height;
    }).toBe(true);
    await opener.press('Enter');
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    const insideViewport = () => dialog.evaluate(node => {
      const box = node.getBoundingClientRect();
      return box.top >= 7.5 && box.bottom <= window.innerHeight - 7.5;
    });
    await expect.poll(insideViewport).toBe(true);
    let refused = 0;
    await page.route(`**/api/v1/books/${id}/read`, route => {
      refused++;
      return route.fulfill({ status: 503, json: { error: { code: 'unavailable', message: 'Owned failure injection' } } });
    });
    await dialog.getByRole('button', { name: t('Mark as read'), exact: true }).click();
    await expect(dialog.getByText(errorText, { exact: true })).toBeVisible();
    expect(refused).toBe(1);
    await expect.poll(insideViewport).toBe(true);
    const viewport = page.viewportSize()!;
    await page.setViewportSize({ width: viewport.width, height: viewport.height - 60 });
    await expect.poll(insideViewport).toBe(true);
    await expect(dialog.getByText(errorText, { exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(opener).toBeFocused();
  } finally {
    await page.unrouteAll({ behavior: 'wait' });
    expect((await post(page, `/api/v1/shelves/${shelf.id}/delete`, {})).ok()).toBeTruthy();
  }
});
