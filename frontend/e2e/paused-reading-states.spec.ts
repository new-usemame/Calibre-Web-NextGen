import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

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

test('a personal pause survives automatic progress and both filtered search paths', async ({ secondaryUser }, info) => {
  const page = secondaryUser.page;
  await page.setViewportSize(info.project.use.viewport ?? { width: 1280, height: 800 });
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

test('Classic pause and explicit resume are reflected in the New UI', async ({ secondaryUser }, info) => {
  const page = secondaryUser.page;
  await page.setViewportSize(info.project.use.viewport ?? { width: 1280, height: 800 });
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

test('a failed status save keeps the saved choice, associates the error and permits retry', async ({ secondaryUser }, info) => {
  const page = secondaryUser.page;
  await page.setViewportSize(info.project.use.viewport ?? { width: 1280, height: 800 });
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
